"""
Merge results/tmp/*.csv (1st pass) into judge/results.json and judge/results.csv.
Then overlay results/tmp_refilter/*.csv (2nd pass): refilter scores overwrite 1st pass for those names.
If judge/names.json exists: output order follows names.json and missing names get score=NaN.
CSV gets a second_refilter column: True if that row was overwritten by refilter data, else False.
"""
import csv
import json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = SCRIPT_DIR  # results/
TMP_DIR = RESULTS_DIR / "tmp"
TMP_REFILTER_DIR = RESULTS_DIR / "tmp_refilter"
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

    # 2b. Collect tmp_refilter/*.csv (2nd pass) and build name -> {score, updated_at}
    refilter_by_name = {}
    if TMP_REFILTER_DIR.exists():
        refilter_files = []
        for p in TMP_REFILTER_DIR.iterdir():
            if p.suffix.lower() == ".csv" and p.stem.isdigit():
                refilter_files.append((int(p.stem), p))
        refilter_files.sort(key=lambda x: x[0])
        for _, path in refilter_files:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    name = row.get("name", "")
                    score_val = row.get("score", "")
                    if score_val == "" or (isinstance(score_val, str) and score_val.strip().lower() == "nan"):
                        score = None
                    else:
                        try:
                            score = float(score_val)
                        except (ValueError, TypeError):
                            score = None
                    refilter_by_name[name] = {"score": score, "updated_at": row.get("updated_at", "")}
    if refilter_by_name:
        print(f"Refilter overlay: {len(refilter_by_name)} names from tmp_refilter/")

    # 3. Build output: with or without names.json; overlay refilter when present
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
                row = dict(row)
            else:
                row = {"name": name, "score": None, "updated_at": now}
            if name in refilter_by_name:
                row["score"] = refilter_by_name[name]["score"]
                row["updated_at"] = refilter_by_name[name]["updated_at"]
                row["second_refilter"] = True
            else:
                row["second_refilter"] = False
            output_rows.append(row)
        missing_count = len(name_list) - len(result_by_name)
        if missing_count > 0:
            print(f"Added {missing_count} missing names with score=NaN")
    else:
        # No names.json: just use merged rows in order; add second_refilter
        output_rows = []
        for row in merged:
            r = dict(row)
            r["second_refilter"] = r["name"] in refilter_by_name
            if r["second_refilter"]:
                r["score"] = refilter_by_name[r["name"]]["score"]
                r["updated_at"] = refilter_by_name[r["name"]]["updated_at"]
            output_rows.append(r)
        print(f"names.json not found; merged {len(merged)} rows as-is")

    # 4. Write results.json: same score/updated_at as output_rows (refilter overlay already applied; no second_refilter field)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    json_rows = [{"name": r["name"], "score": r["score"], "updated_at": r["updated_at"]} for r in output_rows]
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(json_rows, f, ensure_ascii=False, indent=2)

    # 5. Write results.csv with second_refilter column (True/False)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "score", "updated_at", "second_refilter"], extrasaction="ignore")
        w.writeheader()
        for row in output_rows:
            out = dict(row)
            if out.get("score") is None:
                out["score"] = "NaN"
            out["second_refilter"] = row["second_refilter"]
            w.writerow(out)

    print(f"Merged {len(csv_files)} tmp CSVs → {len(output_rows)} rows")
    print(f"  → {OUTPUT_JSON}")
    print(f"  → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
