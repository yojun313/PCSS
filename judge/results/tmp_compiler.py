"""
Merge results/tmp/*.csv (0.csv, 1000.csv, ...) into judge/results.json and judge/results.csv.
If judge/names.json exists: output order follows names.json and missing names get score=NaN.
If names.json is missing: output is the merged tmp rows as-is.
"""
import csv
import json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = SCRIPT_DIR  # results/
TMP_DIR = RESULTS_DIR / "tmp"
JUDGE_DIR = RESULTS_DIR.parent
NAMES_PATH = JUDGE_DIR / "names.json"
OUTPUT_JSON = JUDGE_DIR / "results.json"
OUTPUT_CSV = JUDGE_DIR / "results.csv"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    # 1. Collect numeric tmp/*.csv and sort by number
    csv_files = []
    for p in TMP_DIR.iterdir():
        if p.suffix.lower() == ".csv" and p.stem.isdigit():
            csv_files.append((int(p.stem), p))
    csv_files.sort(key=lambda x: x[0])

    # 2. Read all rows from tmp CSVs in order
    merged = []
    for _, path in csv_files:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                name = row.get("name", "")
                score_val = row.get("score", "")
                if score_val == "" or score_val == "NaN" or (isinstance(score_val, str) and score_val.strip().lower() == "nan"):
                    score = None
                else:
                    try:
                        score = float(score_val)
                    except (ValueError, TypeError):
                        score = None
                merged.append({
                    "name": name,
                    "score": score,
                    "updated_at": row.get("updated_at", ""),
                })

    # 3. Build output: with or without names.json
    now = datetime.now().isoformat()
    if NAMES_PATH.exists():
        names_data = load_json(NAMES_PATH)
        if not isinstance(names_data, list):
            names_data = [names_data]
        name_list = []
        for item in names_data:
            if isinstance(item, dict) and "name" in item:
                name_list.append(item["name"])
            elif isinstance(item, str):
                name_list.append(item)
        result_by_name = {row["name"]: row for row in merged}
        output_rows = []
        for name in name_list:
            row = result_by_name.get(name)
            if row is not None:
                output_rows.append(row)
            else:
                output_rows.append({"name": name, "score": None, "updated_at": now})
        missing_count = len(name_list) - len(result_by_name)
        if missing_count > 0:
            print(f"Added {missing_count} missing names with score=NaN")
    else:
        # No names.json: just use merged rows in order
        output_rows = merged
        print(f"names.json not found; merged {len(merged)} rows as-is")

    # 6. Write results.json
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output_rows, f, ensure_ascii=False, indent=2)

    # 7. Write results.csv (NaN string for null score)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "score", "updated_at"], extrasaction="ignore")
        w.writeheader()
        for row in output_rows:
            out = dict(row)
            if out.get("score") is None:
                out["score"] = "NaN"
            w.writerow(out)

    print(f"Merged {len(csv_files)} tmp CSVs → {len(output_rows)} rows")
    print(f"  → {OUTPUT_JSON}")
    print(f"  → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
