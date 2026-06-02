from typing import Any, Dict, List, Optional
from app.db.mongo import get_papers_col
import re


def _clean_authors(authors: List[str]) -> List[str]:
    # 기존 코드처럼 숫자 제거 + strip
    cleaned = []
    for a in authors:
        a2 = re.sub(r"\d+", "", (a or "")).strip()
        if a2:
            cleaned.append(a2)
    return cleaned


async def compute_author_stats_mongo(
    target_pid: str,  # [변경] 검색 기준이 PID가 됨
    conf_list: Optional[List[str]] = None,
    startyear: Optional[int] = None,
    endyear: Optional[int] = None,
) -> Dict[str, Any]:

    if not target_pid:
        return {"stats": (0, 0, 0, 0), "total": 0, "papers": []}

    col = await get_papers_col()

    # [수정] author_names(문자열배열) 대신 author_pids(ID배열)로 매칭
    match: Dict[str, Any] = {"author_pids": target_pid}

    if conf_list:
        match["conference"] = {"$in": conf_list}

    if startyear is not None and endyear is not None:
        years_str = [str(y) for y in range(startyear, endyear + 1)]
        years_int = list(range(startyear, endyear + 1))
        match["$or"] = [{"year": {"$in": years_str}}, {"year": {"$in": years_int}}]

    # [수정] Aggregation Pipeline: author_name -> author_pids 로 변경
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "first_author": {
                    "$sum": {
                        "$cond": [
                            # author_pids의 0번째 요소가 target_pid와 같은지 확인
                            {
                                "$eq": [
                                    {"$arrayElemAt": ["$author_pids", 0]},
                                    target_pid,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "first_or_second_author": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {
                                        "$eq": [
                                            {"$arrayElemAt": ["$author_pids", 0]},
                                            target_pid,
                                        ]
                                    },
                                    {
                                        "$eq": [
                                            {"$arrayElemAt": ["$author_pids", 1]},
                                            target_pid,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "last_author": {
                    "$sum": {
                        "$cond": [
                            {
                                "$eq": [
                                    {"$arrayElemAt": ["$author_pids", -1]},
                                    target_pid,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                # co_author: author_pids 배열 내 인덱스 확인
                "co_author": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$gt": [
                                            {
                                                "$indexOfArray": [
                                                    "$author_pids",
                                                    target_pid,
                                                ]
                                            },
                                            1,  # 0(1저자), 1(2저자) 보다 커야 함
                                        ]
                                    },
                                    {
                                        "$lt": [
                                            {
                                                "$indexOfArray": [
                                                    "$author_pids",
                                                    target_pid,
                                                ]
                                            },
                                            {
                                                "$subtract": [
                                                    {"$size": "$author_pids"},
                                                    1,  # 마지막 저자 인덱스보다 작아야 함
                                                ]
                                            },
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]

    row = None
    async for r in col.aggregate(pipeline, allowDiskUse=True):
        row = r
        break

    if not row:
        result = {"stats": (0, 0, 0, 0), "total": 0, "papers": []}
        return result

    fa = int(row.get("first_author", 0))
    fs = int(row.get("first_or_second_author", 0))
    la = int(row.get("last_author", 0))
    co = int(row.get("co_author", 0))

    result: Dict[str, Any] = {
        "stats": (fa, fs, la, co),
        "total": co,
    }

    # [주의] 저자 목록을 화면에 보여줄 때는 여전히 이름이 필요하므로 author_names(또는 author_name) 가져옴
    proj = {
        "_id": 0,
        "title": 1,
        "author_names": 1,  # DB 필드명 확인 필요 (보통 표시용 이름 배열)
        "author_pids": 1,  # 검증용으로 가져올 수 있음
        "conference": 1,
        "year": 1,
        "dblp_url": 1,
        "source": 1,
    }

    cursor = col.find(match, proj)
    papers = []
    async for d in cursor:
        title = (d.get("title") or "").strip()
        authors_names = d.get("author_names") or d.get("author_name") or []
        conf = d.get("conference") or ""
        year = d.get("year")

        papers.append(
            {
                "title": title,
                "authors": _clean_authors(authors_names),
                "conference": conf,
                "year": year,
                "dblp_url": d.get("dblp_url", ""),
                "source": d.get("source", ""),
            }
        )

    result["papers"] = papers

    return result
