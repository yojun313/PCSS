"""기존 dblp 컬렉션의 개인 페이지(www) 레코드로 authors 의 소속 · 홈페이지를 채운다 (1회용).

월간 업데이트(update_papers.build_name_to_pid_index)가 같은 필드를 채우므로,
다음 월간 업데이트 전까지 기다리지 않고 바로 쓰고 싶을 때만 실행한다.

    uv run python -m update.backfill_affiliations
"""

from pymongo import UpdateOne
from rich.console import Console

from app.db import authors_col, dblp_col
from app.libs.dblp_profile import profile_from_www

BATCH_SIZE = 5000
console = Console()


def main():
    cursor = dblp_col.find(
        {"type": "www", "title": "Home Page", "key": {"$regex": "^homepages/"}},
        {"_id": 0, "key": 1, "note": 1, "url": 1},
        batch_size=BATCH_SIZE,
    )

    ops, scanned, with_aff, matched = [], 0, 0, 0
    with console.status("dblp 개인 페이지 스캔 중...") as status:
        for rec in cursor:
            scanned += 1
            pid = rec["key"][len("homepages/") :].strip()
            profile = profile_from_www(rec)
            if not pid or not (profile["affiliations"] or profile["urls"]):
                continue
            if profile["affiliations"]:
                with_aff += 1
            ops.append(UpdateOne({"pid": pid}, {"$set": profile}))

            if len(ops) >= BATCH_SIZE:
                matched += authors_col.bulk_write(ops, ordered=False).matched_count
                ops = []
                status.update(
                    f"스캔 {scanned:,} · 소속 {with_aff:,} · 반영 {matched:,}"
                )

    if ops:
        matched += authors_col.bulk_write(ops, ordered=False).matched_count

    console.print(
        f"[green]완료[/green] 스캔 {scanned:,}명 · 소속 있음 {with_aff:,}명 · authors 반영 {matched:,}명"
    )


if __name__ == "__main__":
    main()
