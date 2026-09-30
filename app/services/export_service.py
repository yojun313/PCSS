"""검색 결과 내보내기 (Excel / CSV).

결과 화면의 필터(이름 검색 · 최소 확률 · 학회)를 그대로 받아, 화면에 보이는 것과 같은 범위만 내보낸다.
"""

import csv
import io
from collections import defaultdict
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.services.insights_service import split_target

PAPER_HEADERS = [
    "저자",
    "확률",
    "소속",
    "연도",
    "학회",
    "제목",
    "전체 저자",
    "논문 링크",
    "DBLP 프로필",
]
AUTHOR_HEADERS = [
    "저자",
    "확률",
    "소속",
    "검색 결과 논문 수",
    "최근 연도",
    "학회",
    "DBLP 프로필",
]


def _dblp_profile(pid: str) -> str:
    return f"https://dblp.org/pid/{pid}.html" if pid else ""


def build_export_rows(
    result: Dict[Any, Dict[str, Any]],
    meta: Dict[str, Dict[str, Any]],
    q: str = "",
    min_score: Optional[float] = None,
    conference: str = "",
) -> Dict[str, List[List[Any]]]:
    q = (q or "").strip().lower()
    paper_rows: List[List[Any]] = []
    by_author: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "last_year": 0, "confs": set()}
    )

    papers = sorted(
        (result or {}).values(),
        key=lambda p: (
            -int(p.get("year") or 0),
            str(p.get("conference", "")),
            str(p.get("title", "")),
        ),
    )
    for paper in papers:
        if conference and paper.get("conference") != conference:
            continue
        for tag in paper.get("target_author") or []:
            m = meta.get(tag) or {}
            name, score = split_target(tag)
            name = m.get("name", name)
            score = m.get("score", score)
            if q and q not in name.lower():
                continue
            if min_score is not None and (score is None or score < min_score):
                continue

            year = paper.get("year")
            paper_rows.append(
                [
                    name,
                    score,
                    m.get("affiliation", ""),
                    year,
                    paper.get("conference", ""),
                    paper.get("title", ""),
                    ", ".join(paper.get("author_names") or []),
                    paper.get("source") or "",
                    _dblp_profile(m.get("pid", "")),
                ]
            )

            a = by_author[tag]
            a["count"] += 1
            a["last_year"] = max(a["last_year"], int(year or 0))
            a["confs"].add(paper.get("conference", ""))

    author_rows = []
    for tag, a in by_author.items():
        m = meta.get(tag) or {}
        name, score = split_target(tag)
        author_rows.append(
            [
                m.get("name", name),
                m.get("score", score),
                m.get("affiliation", ""),
                a["count"],
                a["last_year"] or "",
                ", ".join(sorted(c for c in a["confs"] if c)),
                _dblp_profile(m.get("pid", "")),
            ]
        )
    author_rows.sort(key=lambda r: (-r[3], r[0]))

    return {"authors": author_rows, "papers": paper_rows}


def to_xlsx(rows: Dict[str, List[List[Any]]], options_rows: List[List[Any]]) -> bytes:
    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="C80150")

    sheets = [
        ("저자 요약", AUTHOR_HEADERS, rows["authors"], [22, 8, 50, 16, 10, 40, 40]),
        (
            "논문 목록",
            PAPER_HEADERS,
            rows["papers"],
            [22, 8, 40, 8, 12, 60, 60, 40, 40],
        ),
        ("검색 조건", ["항목", "값"], options_rows, [24, 80]),
    ]
    for i, (title, headers, data, widths) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        ws.append(headers)
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
        for r in data:
            ws.append(r)
        for col, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(col)].width = w
        ws.freeze_panes = "A2"
        if data and title != "검색 조건":
            ws.auto_filter.ref = ws.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(rows: Dict[str, List[List[Any]]]) -> bytes:
    # 엑셀에서 한글이 깨지지 않게 UTF-8 BOM
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(PAPER_HEADERS)
    w.writerows(rows["papers"])
    return ("﻿" + buf.getvalue()).encode("utf-8")
