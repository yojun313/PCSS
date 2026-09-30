from lxml import etree
from app.db import conf_col, papers_col, authors_col, name_col
import os
import re
import sys
import requests
import gzip
from pathlib import Path
from rich.progress import (
    Progress,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TextColumn,
    TimeElapsedColumn,
    SpinnerColumn,
)
from rich.panel import Panel
from rich.console import Console
from datetime import datetime, timezone
from pymongo import UpdateOne, ASCENDING
from app.libs.dblp_profile import profile_from_www

# params에 param 추가 시 규칙
"""
    conf_col.params에 들어갈 수 있는 문자열을 룰로 변환.
      - "sp"                  : base=sp (prefix 제한 없이 venue_id=sp 전체 매칭)
      --> "conf/sp"             : prefix=conf + base=sp (key가 conf/... 인 것만 매칭)
      --> "pacmpl:PLDI"         : base=pacmpl + article의 number="PLDI"일 때만 매칭
      - "journals/pacmpl:PLDI": prefix=journals + base=pacmpl (journals/... 인 것만)
      - "pacmmod:3:1"         : base=pacmmod + volume/number
      --> "pacmmod:3:1:2025"    : base=pacmmod + volume/number/year
      - "crossref:...."       : elem.crossref가 정확히 일치할 때 매칭
"""

# =========================
# 설정
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
xml_path = os.path.join(BASE_DIR, "dblp.xml")

BATCH_SIZE = 1000  # MongoDB bulk insert 크기
console = Console()

DB_WRITE_BATCH_SIZE = 10000
console = Console()


def _extract_pid_from_homepages_key(key: str) -> str | None:
    if not key:
        return None
    key = key.strip()
    if not key.startswith("homepages/"):
        return None
    pid = key[len("homepages/") :].strip()
    return pid or None


def ensure_indexes():
    """
    [성능 최적화] pid 필드에 인덱스가 없으면 UpdateOne 속도가 매우 느려집니다.
    반드시 인덱스를 생성해야 합니다.
    """
    try:
        # pid 기준 오름차순 인덱스 (unique=True 권장)
        authors_col.create_index([("pid", ASCENDING)], unique=True)
        # last_seen_at은 삭제 쿼리용
        authors_col.create_index([("last_seen_at", ASCENDING)])
        console.print("[green]DB 인덱스 확인 완료 (pid, last_seen_at)[/green]")
    except Exception as e:
        console.print(f"[yellow]인덱스 생성 중 경고 (무시 가능): {e}[/yellow]")


def build_name_to_pid_index(xml_path: str) -> dict[str, str | list[str]]:
    """
    1. Parsing Phase: XML을 전부 읽어 메모리에 데이터 구축
    2. Writing Phase: 모아둔 데이터를 DB에 일괄 저장
    """
    abs_xml_path = os.path.abspath(xml_path)
    xml_dir = os.path.dirname(abs_xml_path)
    filename_only = os.path.basename(abs_xml_path)
    dtd_path = os.path.join(xml_dir, "dblp.dtd")

    if not os.path.exists(dtd_path):
        console.print(f"[bold red]DTD 파일 없음:[/bold red] {dtd_path}")
        return {}

    # 성능을 위해 인덱스 미리 생성
    ensure_indexes()

    original_cwd = os.getcwd()
    os.chdir(xml_dir)

    name_to_pid: dict[str, str | list[str]] = {}

    # ----------------------------------------------------
    # Phase 1: 메모리에 데이터 추출 (Parsing Only)
    # ----------------------------------------------------
    db_docs = []  # DB에 넣을 딕셔너리들을 모아두는 리스트
    run_ts = datetime.now(timezone.utc)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}[/bold blue]"),
        BarColumn(),
        TextColumn("{task.fields[info]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    context = None
    try:
        etree.DTD(file=dtd_path)
        context = etree.iterparse(
            filename_only,
            events=("end",),
            load_dtd=True,
            huge_tree=True,
            recover=True,
        )

        with progress:
            # Task 1: XML Parsing
            parse_task = progress.add_task(
                "XML Parsing & Memory Build", total=None, info="0 docs"
            )
            scan_count = 0

            for _, elem in context:
                if elem.tag != "www":
                    continue

                scan_count += 1
                if scan_count % 5000 == 0:
                    progress.update(parse_task, info=f"{scan_count:,} items")

                title = (elem.findtext("title") or "").strip()
                if title != "Home Page":
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
                    continue

                key = (elem.get("key") or "").strip()
                pid = _extract_pid_from_homepages_key(key)
                if not pid:
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
                    continue

                # 저자 이름 목록 추출
                authors = []
                for a in elem.findall("author"):
                    val = (a.text or "").strip()
                    if val:
                        authors.append(val)

                if not authors:
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
                    continue

                # --- [메모리 적재 1] DB 저장용 데이터 ---
                primary_name = authors[0]
                # 소속(note type=affiliation)과 홈페이지(url) — 저자 페이지 · 랭킹 · 내보내기에 표시
                profile = profile_from_www(
                    {
                        "note": [
                            {**n.attrib, "text": "".join(n.itertext())}
                            for n in elem.findall("note")
                        ],
                        "url": ["".join(u.itertext()) for u in elem.findall("url")],
                    }
                )
                doc = {
                    "pid": pid,
                    "name": primary_name,
                    # "aliases": authors, # 필요하면 주석 해제
                    "affiliations": profile["affiliations"],
                    "urls": profile["urls"],
                    "updated_at": run_ts,
                    "last_seen_at": run_ts,
                }
                db_docs.append(doc)

                # --- [메모리 적재 2] name_to_pid 인덱스 ---
                mapping_pid = pid
                crossref = (elem.findtext("crossref") or "").strip()
                if crossref.startswith("homepages/"):
                    primary_pid = _extract_pid_from_homepages_key(crossref)
                    if primary_pid:
                        mapping_pid = primary_pid

                for name in authors:
                    existing = name_to_pid.get(name)
                    if existing is None:
                        name_to_pid[name] = mapping_pid
                    else:
                        if isinstance(existing, list):
                            if mapping_pid not in existing:
                                existing.append(mapping_pid)
                        else:
                            if existing != mapping_pid:
                                name_to_pid[name] = [existing, mapping_pid]

                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]

            progress.update(
                parse_task, completed=True, info=f"Done. {len(db_docs):,} collected"
            )

            # ----------------------------------------------------
            # Phase 2: DB Bulk Write (Writing Only)
            # ----------------------------------------------------
            total_docs = len(db_docs)
            write_task = progress.add_task(
                "DB Bulk Update", total=total_docs, info="0%"
            )

            total_upserted = 0
            total_modified = 0

            # 리스트를 BATCH_SIZE 단위로 슬라이싱하여 처리
            for i in range(0, total_docs, DB_WRITE_BATCH_SIZE):
                batch = db_docs[i : i + DB_WRITE_BATCH_SIZE]

                # UpdateOne Operation 생성
                ops = [
                    UpdateOne(
                        {"pid": d["pid"]},
                        {"$set": d, "$setOnInsert": {"created_at": run_ts}},
                        upsert=True,
                    )
                    for d in batch
                ]

                if ops:
                    res = authors_col.bulk_write(ops, ordered=False)
                    total_upserted += res.upserted_count
                    total_modified += res.modified_count

                progress.update(
                    write_task,
                    advance=len(batch),
                    info=f"{int((i + len(batch)) / total_docs * 100)}%",
                )

    except Exception:
        console.print("[bold red]오류 발생[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        return {}
    finally:
        os.chdir(original_cwd)
        if context:
            del context

    console.print(
        f"[green]처리 완료[/green]:\n"
        f" - 메모리 인덱스: {len(name_to_pid):,}명\n"
        f" - DB 저장 대상: {len(db_docs):,}명\n"
        f" - 결과 (Upsert: {total_upserted}, Update: {total_modified})"
    )

    # ----------------------------------------------------
    # Phase 3: 동기화 삭제 (Cleanup)
    # ----------------------------------------------------
    console.print("[dim]삭제된 저자 정리 중...[/dim]")
    del_res = authors_col.delete_many({"last_seen_at": {"$ne": run_ts}})
    if del_res.deleted_count > 0:
        console.print(f"[yellow]삭제 완료[/yellow]: {del_res.deleted_count}명")

    return name_to_pid


# Params 기반 룰 파싱/로딩
def _parse_param_to_rule(param: str, conf_name: str) -> dict | None:
    """
    conf_col.params에 들어갈 수 있는 문자열을 룰로 변환.
      - "sp"                  : base=sp (prefix 제한 없이 venue_id=sp 전체 매칭)
      - "conf/sp"             : prefix=conf + base=sp (key가 conf/... 인 것만 매칭)
      - "pacmpl:PLDI"         : base=pacmpl + article의 number="PLDI"일 때만 매칭
      - "journals/pacmpl:PLDI": prefix=journals + base=pacmpl (journals/... 인 것만)
      - "pacmmod:3:1"         : base=pacmmod + article volume/number
      - "pacmmod:3:1:2025"    : base=pacmmod + volume/number/year
      - "crossref:...."       : elem.crossref가 정확히 일치할 때 매칭
    """
    if not param:
        return None

    raw = param.strip()
    if not raw:
        return None

    low = raw.lower()

    # crossref 매칭 (옵션)
    if low.startswith("crossref:"):
        return {
            "kind": "crossref",
            "base": "__crossref__",
            "crossref": low[len("crossref:") :].strip(),
            "conf": conf_name,
            "specificity": 100,
            "raw": raw,
        }

    parts = [p.strip() for p in raw.split(":") if p.strip()]
    if not parts:
        return None

    # prefix/base 분리 지원: "conf/sp" -> prefix="conf", base="sp"
    main = parts[0].lower()
    prefix = None
    base = main
    if "/" in main:
        prefix, base = main.split("/", 1)

    # "pldi" / "conf/pldi"
    if len(parts) == 1:
        return {
            "kind": "base",
            "base": base,
            "prefix": prefix,
            "conf": conf_name,
            "specificity": 20 if prefix else 10,
            "raw": raw,
        }

    # "pacmpl:PLDI" / "journals/pacmpl:PLDI"
    if len(parts) == 2:
        return {
            "kind": "track",
            "base": base,
            "prefix": prefix,
            "number": parts[1].lower(),
            "conf": conf_name,
            "specificity": 55 if prefix else 50,
            "raw": raw,
        }

    # "pacmmod:3:1"
    if len(parts) == 3:
        return {
            "kind": "issue",
            "base": base,
            "prefix": prefix,
            "volume": parts[1],
            "number": parts[2].lower(),
            "conf": conf_name,
            "specificity": 65 if prefix else 60,
            "raw": raw,
        }

    # "pacmmod:3:1:2025"
    if len(parts) >= 4:
        return {
            "kind": "issue_year",
            "base": base,
            "prefix": prefix,
            "volume": parts[1],
            "number": parts[2].lower(),
            "year": parts[3],
            "conf": conf_name,
            "specificity": 75 if prefix else 70,
            "raw": raw,
        }

    return None


def load_conference_rules_index() -> dict[str, list[dict]]:
    """
    반환: base(venue_id) -> [rule, rule, ...]
    rules는 specificity 높은 순으로 정렬되어 더 구체적인 규칙이 우선 적용됨.
    """
    conferences = conf_col.find({}, {"_id": 0, "params": 1, "name": 1})
    index: dict[str, list[dict]] = {}

    for conf in conferences:
        conf_name = conf.get("name")
        for p in conf.get("params") or []:
            rule = _parse_param_to_rule(p, conf_name)
            if not rule:
                continue
            index.setdefault(rule["base"], []).append(rule)

    # 더 구체적인 규칙을 먼저 적용
    for base in list(index.keys()):
        index[base].sort(key=lambda r: r.get("specificity", 0), reverse=True)

    return index


def resolve_conf_name(elem, rules_index: dict[str, list[dict]]) -> str | None:
    """
    elem 하나를 보고 rules_index(params 기반 규칙들)로 어떤 conf인지 결정.
    - prefix가 있는 룰(conf/sp 등)은 key_prefix와 일치할 때만 적용됨
    """
    key = (elem.get("key") or "").strip()

    key_prefix = None
    venue_id = None
    if key:
        parts = key.split("/")
        if len(parts) >= 2:
            key_prefix = parts[0].lower()
            venue_id = parts[1].lower()

    # crossref 규칙이 있으면 먼저 검사
    crossref = (elem.findtext("crossref") or "").strip().lower()
    if crossref and "__crossref__" in rules_index:
        for rule in rules_index["__crossref__"]:
            if rule["kind"] == "crossref" and crossref == rule["crossref"]:
                return rule["conf"]

    if not venue_id:
        return None

    rules = rules_index.get(venue_id)
    if not rules:
        return None

    number = (elem.findtext("number") or "").strip().lower()
    volume = (elem.findtext("volume") or "").strip()
    year = (elem.findtext("year") or "").strip()

    for rule in rules:
        # 룰에 prefix가 지정되어 있으면 key_prefix가 반드시 일치해야 함
        if rule.get("prefix") and rule["prefix"] != key_prefix:
            continue

        kind = rule["kind"]

        if kind == "base":
            return rule["conf"]

        if elem.tag != "article":
            continue

        if kind == "track":
            if number and number == rule["number"]:
                return rule["conf"]

        elif kind == "issue":
            if (
                volume
                and number
                and volume == rule["volume"]
                and number == rule["number"]
            ):
                return rule["conf"]

        elif kind == "issue_year":
            if (
                volume
                and number
                and year
                and volume == rule["volume"]
                and number == rule["number"]
                and year == rule["year"]
            ):
                return rule["conf"]

    return None


# 메인 트랙 판별
def is_main_track(elem, title, booktitle):
    if not title:
        return False

    title_lower = title.lower()
    booktitle_lower = booktitle.lower() if booktitle else ""

    key = elem.get("key", "").lower()
    crossref = elem.findtext("crossref")
    crossref_lower = crossref.lower() if crossref else ""

    # 구조적 필터
    indicators = ["workshop", "adjunct", "companion", "supplement"]
    for ind in indicators:
        if ind in key or ind in crossref_lower:
            return False

    venue_blacklist_patterns = [
        r"\bworkshops?\b",
        r"\btutorials?\b",
        r"\bdoctoral\b",
        r"\bconsortium\b",
        r"\badjunct\b",
        r"\bcompanion\b",
        r"\bdemo\b",
        r"\bdemonstrations?\b",
        r"\bposters?\b",
        r"\bshort papers?\b",
        r"\bphd symposium\b",
        r"\bextended abstracts?\b",
        r"\bchallenge\b",
        r"\bcompetitions?\b",
    ]

    title_blacklist_patterns = [
        r"\bmessage from\b",
        r"\bwelcome\b",
        r"\bpreface\b",
        r"\bforeword\b",
        r"\bkeynote\b",
        r"\bpanel\b",
        r"\bopening remarks\b",
        r"\bcommittee\b",
        r"\bauthor index\b",
        r"\breviewers\b",
        r"\bsession\b",
        r"\bpresentation\b",
        r"\bbest paper awards?\b",
        r"\bprogram\b",
        r"\bcall for papers\b",
    ]

    for pattern in venue_blacklist_patterns:
        if re.search(pattern, booktitle_lower):
            return False

    for pattern in title_blacklist_patterns:
        if re.search(pattern, title_lower):
            return False

    pages = elem.findtext("pages")
    if pages and re.match(r"^[ivxlcdm]+$", pages.lower()):
        return False

    return True


# DBLP 파싱 & DB 적재
def parse_dblp(xml_path, rules_index, name_to_pid, target_confs: list[str]):
    paper_tags = {"inproceedings", "article"}

    abs_xml_path = os.path.abspath(xml_path)
    xml_dir = os.path.dirname(abs_xml_path)
    filename_only = os.path.basename(abs_xml_path)
    dtd_path = os.path.join(xml_dir, "dblp.dtd")

    if not os.path.exists(dtd_path):
        console.print(f"[bold red]DTD 파일 없음:[/bold red] {dtd_path}")
        return

    original_cwd = os.getcwd()
    os.chdir(xml_dir)

    try:
        etree.DTD(file=dtd_path)
        console.print("[green]DTD 로드 성공[/green]")
    except etree.DTDParseError as e:
        console.print(f"[bold red]DTD 파싱 실패:[/bold red] {e}")
        os.chdir(original_cwd)
        return

    # =========================
    #  이번 실행 마킹값
    # =========================
    run_ts = datetime.now(timezone.utc)

    batch_ops = []
    count = 0
    matched_count = 0  # XML에서 룰에 매칭된 논문 수
    context = None

    total_upserted = 0  # DB에 새로 추가된 수
    total_modified = 0  # DB에서 내용이 변경된 수
    total_db_matched = 0  # DB 쿼리 매칭 수 (변경 없음 포함)

    completed = False  # 파싱 정상 완료 여부

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]DBLP 파싱 중[/bold blue]"),
        BarColumn(),
        TextColumn("scan: {task.fields[scan]}"),
        TextColumn("saved: {task.fields[saved]}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    try:
        context = etree.iterparse(
            filename_only,
            events=("end",),
            load_dtd=True,
            huge_tree=True,
            recover=True,
        )

        with progress:
            task_id = progress.add_task("parse", total=None, scan=0, saved=0)

            for event, elem in context:
                if elem.tag in paper_tags:
                    count += 1
                    progress.update(task_id, scan=count)

                    official_conf_name = resolve_conf_name(elem, rules_index)
                    if not official_conf_name:
                        elem.clear()
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                        continue

                    title = elem.findtext("title")
                    booktitle_xml = elem.findtext("booktitle")
                    # venue_str 변수는 사용되지 않으므로 제거하거나 유지해도 무방

                    matched_count += 1
                    progress.update(task_id, saved=matched_count)

                    year = elem.findtext("year")

                    source = elem.findtext("ee")
                    if not source:
                        raw_url = elem.findtext("url")
                        if raw_url:
                            source = f"https://dblp.org/{raw_url}"

                    dblp_rel_path = elem.findtext("url")
                    if dblp_rel_path:
                        dblp_url = f"https://dblp.org/{dblp_rel_path}"
                    else:
                        key = elem.get("key")
                        dblp_url = f"https://dblp.org/rec/{key}.html" if key else ""

                    authors = []
                    author_pids = []

                    for author in elem.findall("author"):
                        name = (author.text or "").strip()
                        if not name:
                            continue

                        authors.append(name)

                        pid = name_to_pid.get(name)
                        # PID 처리 로직
                        if isinstance(pid, list):
                            author_pids.append(None)
                            continue

                        if pid:
                            author_pids.append(pid)
                        else:
                            author_pids.append(None)

                    record = {
                        "title": title,
                        "author_names": authors,
                        "author_pids": author_pids,
                        "conference": official_conf_name,
                        "year": int(year) if year and year.isdigit() else year,
                        "source": source,
                        "dblp_url": dblp_url,
                        "dblp_key": elem.get("key"),
                        "type": elem.tag,
                        "journal": elem.findtext("journal"),
                        "booktitle": elem.findtext("booktitle"),
                        "volume": elem.findtext("volume"),
                        "number": elem.findtext("number"),
                        "crossref": elem.findtext("crossref"),
                    }

                    dblp_key = record.get("dblp_key")
                    if not dblp_key:
                        elem.clear()
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                        continue

                    # =========================
                    # upsert에 last_seen_at 찍기
                    # =========================
                    now = datetime.now(timezone.utc)

                    batch_ops.append(
                        UpdateOne(
                            {"dblp_key": dblp_key},
                            {
                                "$set": {
                                    **record,
                                    "updated_at": now,
                                    "last_seen_at": run_ts,
                                },
                                "$setOnInsert": {
                                    "created_at": now,
                                },
                            },
                            upsert=True,
                        )
                    )

                    if len(batch_ops) >= BATCH_SIZE:
                        res = papers_col.bulk_write(batch_ops, ordered=False)
                        total_upserted += res.upserted_count
                        total_modified += res.modified_count
                        total_db_matched += res.matched_count
                        batch_ops.clear()

                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]

                elif elem.tag == "dblp":
                    elem.clear()

            # 마지막 남은 batch flush
            if batch_ops:
                res = papers_col.bulk_write(batch_ops, ordered=False)
                total_upserted += res.upserted_count
                total_modified += res.modified_count
                total_db_matched += res.matched_count
                batch_ops.clear()

        completed = True  # 정상적으로 끝까지 돈 경우에만 True

    except Exception:
        console.print("[bold red]파싱 중 오류 발생[/bold red]")
        import traceback

        console.print(traceback.format_exc())

    finally:
        os.chdir(original_cwd)
        if context:
            del context

    # =========================
    #  동기화 삭제 단계
    # =========================
    deleted_count = 0
    if completed and target_confs:
        del_filter = {
            "conference": {"$in": target_confs},
            "last_seen_at": {"$ne": run_ts},
        }
        del_res = papers_col.delete_many(del_filter)
        deleted_count = del_res.deleted_count
    else:
        if not completed:
            console.print("[dim]파싱 비정상 종료로 동기화 삭제 스킵[/dim]")

    # =========================
    #  [수정됨] 최종 결과 출력 (추가/수정/삭제 상세)
    # =========================
    console.print(
        Panel(
            f"[bold]논문 동기화 결과 리포트[/bold]\n"
            f"──────────────────────────────\n"
            f"• [bold]XML 발견(Target Conf)[/bold]: {matched_count:,} 건\n"
            f"• [bold blue]신규 추가(Inserted)[/bold blue]: {total_upserted:,} 건\n"
            f"• [bold green]업데이트(Updated)[/bold green] : {total_modified:,} 건\n"
            f"• [bold red]삭제됨(Deleted)[/bold red]   : {deleted_count:,} 건\n"
            f"• [dim]변경 없음(Skipped)[/dim]  : {matched_count - total_upserted - total_modified:,} 건",
            title="Sync Summary",
            expand=False,
        )
    )


# 다운로드/압축해제
def download_dblp_xml_gz(
    url: str = "https://dblp.uni-trier.de/xml/dblp.xml.gz",
    out_dir: str | Path = os.path.dirname(__file__),
    gz_name: str = "dblp.xml.gz",
    xml_name: str = "dblp.xml",
    chunk_size: int = 1024 * 1024,  # 1MB
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gz_path = out_dir / gz_name
    xml_path = out_dir / xml_name

    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    with progress:
        # 1) 다운로드
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total_size = int(r.headers.get("Content-Length", 0))

            download_task = progress.add_task(
                "Downloading dblp.xml.gz", total=total_size
            )

            with open(gz_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        progress.update(download_task, advance=len(chunk))

        # 2) 압축 해제
        gz_size = gz_path.stat().st_size
        extract_task = progress.add_task("Extracting dblp.xml", total=gz_size)

        with gzip.open(gz_path, "rb") as f_in, open(xml_path, "wb") as f_out:
            while True:
                chunk = f_in.read(chunk_size)
                if not chunk:
                    break
                f_out.write(chunk)
                progress.update(extract_task, advance=len(chunk))

    cleanup_files(gz_path)
    return gz_path, xml_path


def cleanup_files(*paths: Path | str):
    for p in paths:
        try:
            p = Path(p)
            if p.exists():
                p.unlink()
                console.print(f"[dim]Deleted:[/] {p.name}")
        except Exception as e:
            console.print(f"[red]Failed to delete {p}:[/] {e}")


# =========================
# main
# =========================
def main():
    print("=== DBLP 논문 추출 시작 ===")
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "dblp.xml")):
        print("=== DBLP 데이터 다운로드 ===")
        download_dblp_xml_gz()

    print("학회 정보 로드 중...")
    rules_index = load_conference_rules_index()
    if not rules_index:
        print("학회 정보 로드 실패")
        sys.exit(1)

    total_rules = sum(len(v) for v in rules_index.values())
    print(f"로드된 params(rule) 수: {total_rules}")

    # (추가) 이번 실행에서 관리(동기화)할 conf 목록
    target_confs_set = set()
    for rules in rules_index.values():
        for r in rules:
            if r.get("conf"):
                target_confs_set.add(r["conf"])
    target_confs = sorted(target_confs_set)
    print(f"동기화 대상 conference 수: {len(target_confs)}")

    print("PID 인덱스 생성 중(저자 Home Page 레코드)...")
    name_to_pid = build_name_to_pid_index(xml_path)
    if not name_to_pid:
        print(
            "PID 인덱스 생성 실패(혹은 0건). author_url은 검색 링크로만 생성될 수 있음."
        )

    # (변경) target_confs 전달
    parse_dblp(xml_path, rules_index, name_to_pid, target_confs)


if __name__ == "__main__":
    main()
