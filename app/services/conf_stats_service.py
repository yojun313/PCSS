"""학회·연도별 제출/채택 통계.

- 제출 수 · 채택 수는 DBLP 에 없다(DBLP 는 채택된 논문만 담는다). 그래서 학회가 공개한 값을
  `conf_stats` 컬렉션에 따로 저장한다 (update/import_conf_stats.py 로 일괄 적재, 관리자 화면에서 수정).
- DBLP 논문 수는 papers 에서 학회·연도별로 센다. 워크숍 논문이 같이 잡히는 학회가 있어서
  공식 채택 수와 다를 수 있다 - 화면에서는 'DBLP 수록'으로 따로 보여 준다.
- DBLP 집계는 72만 건을 훑어 1초 안팎이라 메모리에 24시간 캐시한다.
"""

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.db import conf_col, conf_stats_col
from app.db.mongo import get_papers_col

_TTL = 24 * 3600
_dblp_cache: Optional[tuple] = None  # (저장 시각, {학회: {연도: 논문 수}})


async def dblp_counts(force: bool = False) -> Dict[str, Dict[int, int]]:
    global _dblp_cache
    if not force and _dblp_cache and time.time() - _dblp_cache[0] < _TTL:
        return _dblp_cache[1]

    col = await get_papers_col()
    # year 가 문자열/정수로 섞여 저장돼 있어 정수로 맞춰 묶는다
    pipeline = [
        {
            "$group": {
                "_id": {
                    "c": "$conference",
                    "y": {"$convert": {"input": "$year", "to": "int", "onError": None}},
                },
                "n": {"$sum": 1},
            }
        }
    ]
    out: Dict[str, Dict[int, int]] = defaultdict(dict)
    async for r in col.aggregate(pipeline):
        c, y = r["_id"].get("c"), r["_id"].get("y")
        if c and y:
            out[c][y] = r["n"]
    _dblp_cache = (time.time(), dict(out))
    return _dblp_cache[1]


async def warm_dblp_counts() -> None:
    try:
        await dblp_counts()
    except Exception as e:
        print(f"[stats] dblp count warm-up failed: {e}")


def _rate(row: Dict[str, Any]) -> Optional[float]:
    s, a = row.get("submitted"), row.get("accepted")
    if s and a is not None and s > 0:
        return round(a / s * 100, 1)
    r = row.get("rate")
    return round(float(r), 1) if r is not None else None


async def build_overview() -> Dict[str, Any]:
    """페이지가 한 번에 받는 데이터: 학회별로 연도 → {제출, 채택, 채택률, DBLP 수록, 출처}."""
    dblp = await dblp_counts()
    stats: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)
    for r in conf_stats_col.find({}, {"_id": 0}):
        stats[r["conference"]][int(r["year"])] = r

    confs = []
    for c in conf_col.find({}, {"_id": 0, "name": 1, "kind": 1}):
        name = (c.get("name") or "").strip()
        if not name or name not in stats:
            continue  # 제출/채택 자료가 하나도 없는 학회는 표에 넣지 않는다
        years = {}
        for y, r in stats[name].items():
            years[y] = {
                "s": r.get("submitted"),
                "a": r.get("accepted"),
                "r": _rate(r),
                "src": r.get("source") or "",
                "conf": r.get("confidence") or "",
                "note": r.get("note") or "",
            }
        for y, n in dblp.get(name, {}).items():
            years.setdefault(y, {})["d"] = n
        confs.append(
            {"name": name, "kind": (c.get("kind") or "").strip(), "years": years}
        )

    confs.sort(key=lambda x: (x["kind"], x["name"]))
    all_years = sorted(
        {y for c in confs for y, v in c["years"].items() if "r" in v or "s" in v}
    )
    return {
        "confs": confs,
        "kinds": sorted({c["kind"] for c in confs}),
        "year_min": all_years[0] if all_years else None,
        "year_max": all_years[-1] if all_years else None,
    }


def list_rows(conference: str = "") -> List[Dict[str, Any]]:
    q = {"conference": conference} if conference else {}
    return list(
        conf_stats_col.find(q, {"_id": 0, "updated_at": 0}).sort(
            [("conference", 1), ("year", -1)]
        )
    )
