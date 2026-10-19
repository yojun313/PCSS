"""학회 제출/채택 통계 일괄 적재.

사용: .venv/bin/python update/import_conf_stats.py FILE.json [FILE2.json ...] [--dry-run]

JSON 은 {"conference","year","submitted","accepted","rate","source","confidence","note"} 객체의 배열.
- 학회 이름은 conferences 컬렉션의 name 과 같아야 한다(다르면 건너뛰고 알려 준다).
- 관리자 화면에서 손댄 행(manual=True)은 덮어쓰지 않는다.
- 같은 (학회, 연도)가 여러 파일에 있으면 공식 출처(official)가 우선, 그다음 뒤에 오는 파일.
- 채택 > 제출처럼 앞뒤가 안 맞는 값은 적재하지 않고 목록으로 보여 준다.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import conf_col, conf_stats_col  # noqa: E402


def _int(v):
    try:
        return int(v) if v is not None else None
    except TypeError, ValueError:
        return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        print(__doc__)
        return

    names = {c["name"].strip() for c in conf_col.find({}, {"name": 1}) if c.get("name")}
    merged, unknown, bad = {}, set(), []
    for path in args:
        for r in json.load(open(path, encoding="utf-8")):
            conf, year = (r.get("conference") or "").strip(), _int(r.get("year"))
            if conf not in names:
                unknown.add(conf)
                continue
            s, a = _int(r.get("submitted")), _int(r.get("accepted"))
            rate = r.get("rate")
            if year is None or (s is None and a is None and rate is None):
                continue
            if s is not None and a is not None and (a > s or s <= 0):
                bad.append((conf, year, s, a, "채택 > 제출"))
                continue
            if rate is not None and not 0 < float(rate) <= 100:
                bad.append((conf, year, s, a, f"rate={rate}"))
                continue
            row = {
                "conference": conf,
                "year": year,
                "submitted": s,
                "accepted": a,
                "rate": float(rate) if rate is not None else None,
                "source": r.get("source") or "",
                "confidence": r.get("confidence") or "",
                "note": r.get("note") or "",
            }
            old = merged.get((conf, year))
            if (
                old
                and old["confidence"] == "official"
                and row["confidence"] != "official"
            ):
                continue
            merged[(conf, year)] = row

    manual = {
        (d["conference"], d["year"])
        for d in conf_stats_col.find({"manual": True}, {"conference": 1, "year": 1})
    }
    now = datetime.now(timezone.utc)
    n = skipped = 0
    for key, row in merged.items():
        if key in manual:
            skipped += 1
            continue
        n += 1
        if not dry:
            conf_stats_col.update_one(
                {"conference": row["conference"], "year": row["year"]},
                {"$set": {**row, "updated_at": now}, "$setOnInsert": {"manual": False}},
                upsert=True,
            )
    print(
        f"{'(dry-run) ' if dry else ''}적재 {n}건, 수동 수정 보호로 건너뜀 {skipped}건"
    )
    if unknown:
        print("DB에 없는 학회 이름(건너뜀):", ", ".join(sorted(unknown)))
    for b in bad:
        print("이상한 값(건너뜀):", b)


if __name__ == "__main__":
    main()
