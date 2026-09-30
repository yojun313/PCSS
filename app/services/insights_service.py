"""저자 페이지 · 검색 결과 · 랭킹이 함께 쓰는 집계.

- 저자 역할(role): 한 논문에서 저자가 몇 번째인지로 나눈다. 겹치는 경우 우선순위는
  1저자 > 마지막 저자 > 2저자 > 기타 공저자 (2인 논문의 2번째 저자는 보통 지도교수라 '마지막 저자').
  차트처럼 한 논문을 한 칸에만 넣어야 하는 곳에서 쓴다. 기존 통계표(1저자 / 1·2저자 / 마지막 / 기타)는
  겹침을 허용하는 기존 정의를 그대로 유지한다.
- 논문 데이터는 월 1회 갱신되므로 랭킹처럼 무거운 집계는 메모리에 캐시한다.
"""

import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.data import get_conferences_for_ui, name_dict
from app.db.mongo import get_collection, get_papers_col
from app.libs.dblp_profile import current_affiliation

ROLES = ("first", "last", "second", "middle")
ROLE_LABELS = {
    "first": "1저자",
    "last": "마지막 저자",
    "second": "2저자",
    "middle": "기타 공저자",
}

_DIGIT_SUFFIX = re.compile(r"\d+")


def clean_name(name: str) -> str:
    # DBLP 동명이인 번호("Hong Kim 0002") 제거 — name_dict 키와 같은 규칙
    return _DIGIT_SUFFIX.sub("", name or "").strip()


def name_score(name: str) -> Optional[float]:
    v = name_dict.get(clean_name(name))
    return float(v) if isinstance(v, (int, float)) else None


def role_of(idx: int, n: int) -> str:
    if idx == 0:
        return "first"
    if idx == n - 1:
        return "last"
    if idx == 1:
        return "second"
    return "middle"


def _to_int_year(v: Any) -> Optional[int]:
    try:
        return int(v)
    except TypeError, ValueError:
        return None


def _year_match(start: int, end: int) -> Dict[str, Any]:
    # year 가 문자열/정수로 섞여 저장돼 있다 (search_service._year_filter 와 동일)
    years = list(range(start, end + 1))
    return {
        "$or": [{"year": {"$in": years}}, {"year": {"$in": [str(y) for y in years]}}]
    }


# ---------------------------------------------------------------------------
# 저자 프로필 (소속 · 홈페이지)
# ---------------------------------------------------------------------------


async def get_profiles(pids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    pids = [p for p in set(pids) if p]
    if not pids:
        return {}
    authors = await get_collection("authors")
    out = {}
    cursor = authors.find(
        {"pid": {"$in": pids}},
        {"_id": 0, "pid": 1, "name": 1, "affiliations": 1, "urls": 1},
    )
    async for d in cursor:
        d["affiliation"] = current_affiliation(d)
        out[d["pid"]] = d
    return out


def profile_links(urls: List[str]) -> List[Dict[str, str]]:
    """DBLP 개인 페이지 url 목록 -> 표시용 [{label, url}]"""
    known = (
        ("orcid.org", "ORCID"),
        ("scholar.google", "Google Scholar"),
        ("wikipedia.org", "Wikipedia"),
        ("wikidata.org", "Wikidata"),
        ("github.com", "GitHub"),
        ("linkedin.com", "LinkedIn"),
        ("researchgate.net", "ResearchGate"),
        ("semanticscholar.org", "Semantic Scholar"),
    )
    links = []
    for u in urls or []:
        label = next((name for host, name in known if host in u), "")
        if not label:
            host = re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
            label = f"홈페이지 ({host})"
        links.append({"label": label, "url": u})
    return links


# ---------------------------------------------------------------------------
# 저자 페이지
# ---------------------------------------------------------------------------


async def compute_author_page(pid: str) -> Dict[str, Any]:
    col = await get_papers_col()
    proj = {
        "_id": 0,
        "title": 1,
        "author_names": 1,
        "author_pids": 1,
        "conference": 1,
        "year": 1,
        "dblp_url": 1,
        "source": 1,
    }

    papers: List[Dict[str, Any]] = []
    fa = fs = la = co = 0
    yearly: Dict[int, Counter] = defaultdict(Counter)
    venues: Counter = Counter()
    coauthors: Dict[str, Dict[str, Any]] = {}
    display_name = ""

    async for d in col.find({"author_pids": pid}, proj):
        pids = d.get("author_pids") or []
        names = d.get("author_names") or []
        if pid not in pids:
            continue
        idx = pids.index(pid)
        n = len(pids)
        year = _to_int_year(d.get("year"))
        conf = d.get("conference") or ""
        role = role_of(idx, n)

        if not display_name and idx < len(names):
            display_name = clean_name(names[idx])

        # 기존 통계표 정의 (겹침 허용)
        fa += idx == 0
        fs += idx in (0, 1)
        la += idx == n - 1
        co += 1 < idx < n - 1

        if year is not None:
            yearly[year][role] += 1
        venues[conf] += 1

        for j, other in enumerate(pids):
            if j == idx or not other:
                continue
            c = coauthors.setdefault(
                other,
                {
                    "pid": other,
                    "name": clean_name(names[j]) if j < len(names) else other,
                    "count": 0,
                    "last_year": None,
                },
            )
            c["count"] += 1
            if year is not None and (c["last_year"] is None or year > c["last_year"]):
                c["last_year"] = year

        papers.append(
            {
                "title": (d.get("title") or "").strip(),
                "authors": [clean_name(a) for a in names],
                "author_pids": pids,
                "conference": conf,
                "year": year,
                "role": role,
                "dblp_url": d.get("dblp_url") or "",
                "source": d.get("source") or "",
            }
        )

    # 연도 축은 빈 해도 0으로 채워야 공백기가 보인다
    yearly_rows = []
    if yearly:
        for y in range(min(yearly), max(yearly) + 1):
            row = {"year": y, **{r: yearly[y][r] for r in ROLES}}
            row["total"] = sum(row[r] for r in ROLES)
            yearly_rows.append(row)

    top_coauthors = sorted(
        coauthors.values(),
        key=lambda c: (-c["count"], -(c["last_year"] or 0), c["name"]),
    )[:30]
    for c in top_coauthors:
        c["score"] = name_score(c["name"])

    profiles = await get_profiles([pid] + [c["pid"] for c in top_coauthors])
    for c in top_coauthors:
        c["affiliation"] = profiles.get(c["pid"], {}).get("affiliation", "")

    profile = profiles.get(pid, {})
    if not display_name:
        display_name = clean_name(profile.get("name", "")) or pid

    return {
        "pid": pid,
        "name": display_name,
        "profile": profile,
        "links": profile_links(profile.get("urls") or []),
        "stats": (fa, fs, la, co),
        "total": len(papers),
        "papers": papers,
        "yearly": yearly_rows,
        "venues": venues.most_common(),
        "coauthors": top_coauthors,
    }


# ---------------------------------------------------------------------------
# 검색 결과 보강 (저자별 pid · 소속 · 점수)
# ---------------------------------------------------------------------------

_SCORE_SUFFIX = re.compile(r"\s*\((\d+(?:\.\d+)?|None)\)\s*$")


def split_target(tag: str) -> Tuple[str, Optional[float]]:
    """'Hanbin Hong (0.9)' -> ('Hanbin Hong', 0.9)"""
    m = _SCORE_SUFFIX.search(tag or "")
    if not m:
        return (tag or "").strip(), None
    name = tag[: m.start()].strip()
    try:
        return name, float(m.group(1))
    except ValueError:
        return name, None


async def result_author_meta(
    result: Dict[Any, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """검색 결과의 target_author 태그 -> {name, score, pid, affiliation}"""
    meta: Dict[str, Dict[str, Any]] = {}
    for paper in (result or {}).values():
        names = paper.get("author_names") or []
        pids = paper.get("author_pids") or []
        for tag in paper.get("target_author") or []:
            if tag in meta and meta[tag]["pid"]:
                continue
            name, score = split_target(tag)
            pid = (
                pids[names.index(name)]
                if name in names and names.index(name) < len(pids)
                else ""
            )
            meta[tag] = {"name": name, "score": score, "pid": pid, "affiliation": ""}

    profiles = await get_profiles(m["pid"] for m in meta.values())
    for m in meta.values():
        m["affiliation"] = profiles.get(m["pid"], {}).get("affiliation", "")
    return meta


# ---------------------------------------------------------------------------
# 랭킹
# ---------------------------------------------------------------------------

RANKING_METRICS = {
    "first": ("1저자", "first_w"),
    "first_second": ("1·2저자", "fs_w"),
    "last": ("마지막 저자", "last_w"),
    "all": ("전체", "total_w"),
}

_CACHE_TTL_SEC = 24 * 3600  # 논문 데이터는 월 1회 갱신
_ranking_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}


def ranking_kinds() -> List[str]:
    return sorted({c["kind"] for c in get_conferences_for_ui() if c.get("kind")})


def _confs_for_kind(kind: str) -> List[str]:
    return sorted(
        c["conference"]
        for c in get_conferences_for_ui()
        if kind in ("", "all") or c.get("kind") == kind
    )


async def _window_counts(
    confs: List[str], start: int, end: int
) -> List[Dict[str, Any]]:
    """기간 안 논문으로 저자(pid)별 역할 카운트. 한국인 판정 전이라 전 저자를 센다."""
    key = ("window", tuple(confs), start, end)
    hit = _ranking_cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL_SEC:
        return hit[1]

    col = await get_papers_col()
    pipeline = [
        {"$match": {"conference": {"$in": confs}, **_year_match(start, end)}},
        {
            "$project": {
                "_id": 0,
                "author_pids": 1,
                "author_names": 1,
                "conference": 1,
                "n": {"$size": {"$ifNull": ["$author_pids", []]}},
            }
        },
        {"$unwind": {"path": "$author_pids", "includeArrayIndex": "i"}},
        {
            "$group": {
                "_id": "$author_pids",
                "name": {"$first": {"$arrayElemAt": ["$author_names", "$i"]}},
                "total_w": {"$sum": 1},
                "first_w": {"$sum": {"$cond": [{"$eq": ["$i", 0]}, 1, 0]}},
                "fs_w": {"$sum": {"$cond": [{"$lte": ["$i", 1]}, 1, 0]}},
                "last_w": {
                    "$sum": {"$cond": [{"$eq": ["$i", {"$subtract": ["$n", 1]}]}, 1, 0]}
                },
                "confs": {"$push": "$conference"},
            }
        },
    ]
    rows = []
    async for r in col.aggregate(pipeline, allowDiskUse=True):
        r["pid"] = r.pop("_id")
        r["name"] = clean_name(r.get("name") or "")
        rows.append(r)

    _ranking_cache[key] = (time.time(), rows)
    return rows


async def _first_years(confs: List[str], pids: List[str]) -> Dict[str, int]:
    """PCSS 대상 학회에서의 첫 논문 연도 (신진 연구자 판별용)."""
    if not pids:
        return {}
    key = ("first_year", tuple(confs), tuple(sorted(pids)))
    hit = _ranking_cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL_SEC:
        return hit[1]

    col = await get_papers_col()
    pipeline = [
        {"$match": {"author_pids": {"$in": pids}, "conference": {"$in": confs}}},
        {"$project": {"_id": 0, "author_pids": 1, "year": 1}},
        {"$unwind": "$author_pids"},
        {"$match": {"author_pids": {"$in": pids}}},
        {
            "$group": {
                "_id": "$author_pids",
                "first_year": {
                    "$min": {
                        "$convert": {"input": "$year", "to": "int", "onError": None}
                    }
                },
            }
        },
    ]
    out = {}
    async for r in col.aggregate(pipeline, allowDiskUse=True):
        if r.get("first_year") is not None:
            out[r["_id"]] = int(r["first_year"])
    _ranking_cache[key] = (time.time(), out)
    return out


async def compute_rankings(
    kind: str,
    start: int,
    end: int,
    metric: str,
    threshold: float,
    newcomer: bool,
    limit: int = 100,
) -> Dict[str, Any]:
    confs = _confs_for_kind(kind)
    if not confs:
        return {"rows": [], "candidates": 0, "confs": []}

    metric_field = RANKING_METRICS.get(metric, RANKING_METRICS["first"])[1]
    rows = await _window_counts(confs, start, end)

    korean = []
    for r in rows:
        if r[metric_field] <= 0:
            continue
        score = name_score(r["name"])
        if score is None or score < threshold:
            continue
        korean.append({**r, "score": score})

    first_years = await _first_years(confs, [r["pid"] for r in korean])
    for r in korean:
        r["first_year"] = first_years.get(r["pid"])
    if newcomer:
        korean = [
            r
            for r in korean
            if r["first_year"] is not None and r["first_year"] >= start
        ]

    korean.sort(key=lambda r: (-r[metric_field], -r["total_w"], r["name"]))
    candidates = len(korean)
    top = korean[:limit]

    profiles = await get_profiles(r["pid"] for r in top)
    rank, prev = 0, None
    for i, r in enumerate(top, start=1):
        # 같은 점수는 같은 순위 (1, 2, 2, 4 ...)
        if r[metric_field] != prev:
            rank, prev = i, r[metric_field]
        r["rank"] = rank
        r["metric"] = r[metric_field]
        r["affiliation"] = profiles.get(r["pid"], {}).get("affiliation", "")
        r["top_venues"] = [c for c, _ in Counter(r["confs"]).most_common(3)]

    return {"rows": top, "candidates": candidates, "confs": confs}


async def warm_ranking_cache(periods: Iterable[int] = (5, 3, 10)) -> None:
    """서버 시작 직후 '전체 분야' 집계를 미리 해 둔다 (첫 방문자가 10초 가까이 기다리지 않게)."""
    from datetime import datetime

    end = datetime.now().year
    confs = _confs_for_kind("all")
    for years in periods:
        try:
            await _window_counts(confs, end - years + 1, end)
        except Exception as e:
            print(f"[rankings] cache warm-up failed ({years}y): {e}")
