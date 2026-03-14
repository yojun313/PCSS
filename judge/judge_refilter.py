"""
Extract from results.csv rows where 0.9 <= score < 1 (ambiguous Korean-probability names)
and save to ambiguous.csv for manual review. Later: add a stricter name filter.
"""
import csv
from pathlib import Path

JUDGE_DIR = Path(__file__).resolve().parent
RESULTS_CSV = JUDGE_DIR / "results.csv"
OUTPUT_CSV = JUDGE_DIR / "ambiguous.csv"

MIN_SCORE = 0.9
MAX_SCORE = 1.0  # exclusive


def main():
    if not RESULTS_CSV.exists():
        print(f"Not found: {RESULTS_CSV}")
        return

    rows = []
    with open(RESULTS_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ["name", "score", "updated_at"]
        for row in reader:
            raw = row.get("score", "")
            if raw == "" or str(raw).strip().lower() == "nan":
                continue
            try:
                score = float(raw)
            except (ValueError, TypeError):
                continue
            if MIN_SCORE <= score < MAX_SCORE:
                rows.append(row)

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows (0.9 <= score < 1) -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
