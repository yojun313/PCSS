"""
Re-score ambiguous names (e.g. from ambiguous.csv) with a stricter prompt to separate Korean
from Chinese/Japanese. Uses Grok 4 Batch API. Output: results/tmp_refilter/{num}.csv,
results/results_refilter_grok.json, results/results_refilter_grok.csv.
"""
import csv
import json
import os
import time
import argparse
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from judge_authors_grok import (
    API_KEY,
    BASE_URL,
    ADD_BATCH_SIZE,
    MAX_NAMES_PER_BATCH,
    MAX_POLLS_BEFORE_NEXT_BATCH,
    MAX_DRAIN_POLLS,
    API_RETRIES,
    API_RETRY_BACKOFF,
    console,
    create_batch,
    get_batch_status,
    list_batch_results,
    _request_with_retry,
    extract_score,
    _extract_content_from_batch_result,
    save_json,
    save_csv,
    save_csv_append,
    _append_failed_rows,
)

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

# Refilter uses reasoning model for better discrimination (main judge uses fast-non-reasoning).
REFILTER_MODEL_NAME = "grok-4-1-fast-reasoning"

# Stricter prompt: Korean vs Chinese/Japanese. Standard romanization + overlapping surnames = ambiguous.
REFILTER_SYSTEM_PROMPT = """
You classify romanized names by the probability of being South Korean (0.0–1.0). Output a single number only.

**Context:** Korean names usually follow the Revised Romanization (standard) or McCune-Reischauer. Chinese names use Pinyin (Mainland) or Yale/Jyutping (Hong Kong/Taiwan).

### 1. High Probability (0.90–1.0) — Clearly Korean
- **Standard Surnames:** Kim, Lee, Park, Choi, Jung (or Jeong), Kang, Cho, Yoon, Jang, Shin, Lim (or Im), Han, Oh, Seo, Kwon, Hwang, Song.
- **Korean-style Given Names:** Two-syllable names (often hyphenated or space-separated) using standard Korean vowel combinations:
    - Vowels: 'eo', 'eu', 'ae', 'ui', 'ie'.
    - Syllables: -su, -ho, -hee, -young, -hyeon, -jun, -min, -bin, -woo, -seok, -jae, -hun.
- **Example:** Min-su Kim, Jeong-hun Lee, Ji-hye Park.

### 2. Ambiguous / Low-Mid (0.30–0.50) — Shared or Non-standard
- **Surname "Yi":** While "Yi" can be a traditional Korean spelling for 李(이), it is significantly more common in Chinese (易, 伊) or non-standard. **Score 0.4–0.5 max** even if the given name looks slightly Korean.
- **Single-syllable Given Names:** Surnames like Kim/Lee/Park with a single-syllable given name (e.g., Jin Kim, Sang Lee) are common in both cultures.
- **Westernized:** "Michael Kim", "Jessica Park" — if clearly Korean family name → 0.90; if any doubt → 0.60–0.65. (Do not use 0.80–0.89.)

### 3. Very Low Probability (0.0–0.25) — Likely Chinese/Japanese/Other
- **Non-Korean Syllable Structures (CRITICAL):**
    - **"Chung" with "Yi":** Names like "Da-Chung", "Ka-Ho", "Wing", "Siu", "Wai", "Hing", "Fai" are Cantonese/Taiwanese.
    - **Pinyin Indicators:** "X", "Q", "Z" at the start of syllables (e.g., Xiao, Qiang, Zhang, Zhao, Zhu).
    - **Vowel/Ending Clusters:** Names ending in -ng (except common Korean ones like Jung/Sung/Hong) combined with surnames like Yi, Li, Wang, Chen.
- **"Yi" + Non-standard Vowels:** "Yi" combined with names containing "u" used as "oo" (like "Chung") or "a" used in ways inconsistent with Korean phonology.
- **Example:** Da-Chung Yi (0.1), Xiao-Xing Chen (0.0), Yamamoto (0.0).

### Scoring Logic:
- If Surname is "Yi", start at 0.4. If the given name has non-Korean phonetic patterns (e.g., "Chung", "Wah"), drop to 0.1–0.2.
- If Surname is "Lee" but given name is Pinyin-style (e.g., "Liang", "Wei"), score 0.2.
- If Surname is "Kim/Park" and given name is 2-syllable Korean style, score 0.95+.

**Critical — avoid 0.85–0.89:** If you are tempted to score 0.85 or above, pause and reconsider. Then output **either**:
- **0.90–1.0** (clearly Korean: standard surname + clearly Korean given name), or
- **0.60–0.69** (ambiguous: could be Korean but not confident — e.g. single-syllable given name, or surname/given name that could be Chinese).
Do NOT output 0.80–0.89. Borderline cases → 0.6x; only clearly Korean → 0.9x.

Return ONLY the number.
"""

# Extra names always included in test/limit runs to verify filter (expect: low score — not Korean).
EXTRA_REFILTER_TEST_NAMES = ["Andong Lu", "Handong Ye", "Han Yang", "Yang Yang"]

# For --make-ambiguous: score range to extract (0.9 <= score < 1 = ambiguous band).
AMBIGUOUS_MIN_SCORE = 0.7
AMBIGUOUS_MAX_SCORE = 1.0  # exclusive


def make_ambiguous_csv(
    source_csv: str | Path,
    output_csv: str | Path,
    judge_dir: Path,
) -> None:
    """Build ambiguous.csv from source CSV: 0.9 <= score < 1 and second_refilter is False only."""
    source_path = judge_dir / source_csv if isinstance(source_csv, str) else Path(source_csv)
    out_path = judge_dir / output_csv if isinstance(output_csv, str) else Path(output_csv)
    if not source_path.exists():
        console.print(f"[red]Not found: {source_path}[/]")
        return
    rows = []
    with open(source_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or ["name", "score", "updated_at"])
        if "second_refilter" not in fieldnames:
            fieldnames.append("second_refilter")
        for row in reader:
            raw = row.get("score", "")
            if raw == "" or str(raw).strip().lower() == "nan":
                continue
            try:
                score = float(raw)
            except (ValueError, TypeError):
                continue
            if not (AMBIGUOUS_MIN_SCORE <= score < AMBIGUOUS_MAX_SCORE):
                continue
            # Only include rows that have NOT been refiltered yet (second_refilter == False)
            sr = row.get("second_refilter", "")
            if str(sr).strip().lower() in ("true", "1", "yes"):
                continue
            rows.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "score", "updated_at"], extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in ["name", "score", "updated_at"]})
    console.print(f"[green]Wrote {len(rows)} rows (0.9 <= score < 1, second_refilter=False) → {out_path}[/]")


def add_batch_requests_refilter(batch_id: str, items: list[dict], api_key: str) -> None:
    """Same as add_batch_requests but uses REFILTER_SYSTEM_PROMPT."""
    batch_requests = []
    for it in items:
        batch_requests.append({
            "batch_request_id": it["batch_request_id"],
            "batch_request": {
                "chat_get_completion": {
                    "model": REFILTER_MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": REFILTER_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Name: '{it['name']}'"},
                    ],
                    "temperature": 0.0,
                }
            },
        })
    r = _request_with_retry(
        "POST",
        f"{BASE_URL}/batches/{batch_id}/requests",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"batch_requests": batch_requests},
        timeout=90,
    )
    r.raise_for_status()


def load_csv_names(path: Path) -> list[dict]:
    """Load list of {name} from CSV with 'name' column."""
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if name:
                out.append({"name": name})
    return out


def load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def run_refilter(
    input_csv: str = "ambiguous.csv",
    output_path: str = "results/results_refilter_grok.json",
    test_mode: bool = False,
    limit: int | None = None,
    add_batch_size: int = ADD_BATCH_SIZE,
    max_names_per_batch: int = MAX_NAMES_PER_BATCH,
    max_polls_before_next: int = MAX_POLLS_BEFORE_NEXT_BATCH,
    max_drain_polls: int = MAX_DRAIN_POLLS,
    debug: bool = False,
):
    judge_dir = Path(__file__).resolve().parent
    input_path = judge_dir / input_csv
    output_path_p = judge_dir / output_path
    results_dir = output_path_p.parent
    chunk_dir = results_dir / "tmp_refilter"
    csv_path_full = output_path_p.with_suffix(".csv")

    if not API_KEY:
        console.print("[bold red]Missing API key. Set GROK_API_KEYS (or XAI_API_KEY) in .env[/]")
        return

    with console.status("[bold yellow]Loading refilter input..."):
        all_items = load_csv_names(input_path)
        already_done = load_json(output_path_p)
        processed_names = {item["name"] for item in already_done}
        target_items = [item for item in all_items if item["name"] not in processed_names]

    if not all_items:
        console.print(f"[red]No names in {input_path}[/]")
        return

    num_skipped = len(processed_names)
    if num_skipped:
        console.print(f"[dim]Already in refilter results (skipped): {num_skipped:,} names[/]")

    # Limit count (like judge_authors_grok --test): --limit N or --test (30)
    if limit is not None:
        target_items = target_items[:limit]
        console.print(f"[bold magenta]Limit: first {limit} names → {len(target_items)} to process.[/]")
    elif test_mode:
        target_items = target_items[:30]
        console.print(f"[bold magenta]Test mode: first 30 names → {len(target_items)} to process.[/]")

    # In test/limit runs, add extra names to verify filter (Andong Lu, Handong Ye → expect low score)
    if test_mode or limit is not None:
        existing = {item["name"] for item in target_items}
        for name in EXTRA_REFILTER_TEST_NAMES:
            if name not in existing and name not in processed_names:
                target_items.append({"name": name})
                existing.add(name)
                console.print(f"[dim]Added refilter test name: {name}[/]")

    total = len(target_items)
    if total == 0:
        console.print("[bold green]✔ All refilter names already processed.[/]")
        return

    console.print(f"[cyan]Input CSV:[/] {input_path} | [green]To process:[/] {total:,}")

    batches_of_names = [
        target_items[i : i + max_names_per_batch]
        for i in range(0, len(target_items), max_names_per_batch)
    ]
    console.print(f"[bold]Batches:[/] {len(batches_of_names)} (max {max_names_per_batch} names each)")
    console.print(f"[dim]Chunk CSVs → {chunk_dir}/  |  Final → {output_path_p}, {csv_path_full}[/]")

    final_so_far = list(already_done)
    total_failed = []
    total_cost_ticks = 0
    total_succeeded = 0
    incomplete_batches = []

    for batch_no, chunk_items in enumerate(batches_of_names):
        n_chunk = len(chunk_items)
        console.print(f"\n[bold cyan]——— Refilter batch {batch_no + 1}/{len(batches_of_names)} ({n_chunk} names) ———[/]")

        batch_name = f"korean_refilter_{batch_no + 1}" + ("_test" if test_mode else "")
        batch_id = create_batch(batch_name, API_KEY)
        console.print(f"  batch_id: [cyan]{batch_id}[/]")

        items_for_add = [
            {"batch_request_id": str(i), "name": chunk_items[i]["name"]}
            for i in range(n_chunk)
        ]
        for start in range(0, len(items_for_add), add_batch_size):
            chunk = items_for_add[start : start + add_batch_size]
            add_batch_requests_refilter(batch_id, chunk, API_KEY)
            console.print(f"  Added {start + 1}–{start + len(chunk)}/{n_chunk}")
            if start + add_batch_size < len(items_for_add):
                time.sleep(0.5)

        console.print(f"[bold]Waiting for batch (max {max_polls_before_next} polls)...[/]")
        all_succeeded = []
        all_failed = []
        global_offset = len(final_so_far)
        poll_count = 0
        batch_done = False
        while poll_count < max_polls_before_next:
            batch = get_batch_status(batch_id, API_KEY)
            state = batch.get("state", {})
            num_pending = state.get("num_pending", 0)
            num_success = state.get("num_success", 0)
            num_error = state.get("num_error", 0)
            num_requests = state.get("num_requests", 0)
            completed = num_success + num_error
            if completed > 0:
                count_before = len(all_succeeded)
                fetched_ids = {s["batch_request_id"] for s in all_succeeded}
                fetched_ids.update(f["batch_request_id"] for f in all_failed)
                pagination_token = None
                while True:
                    page = list_batch_results(batch_id, API_KEY, page_size=add_batch_size, pagination_token=pagination_token)
                    if debug and batch_no == 0 and len(fetched_ids) == 0 and pagination_token is None:
                        debug_path = output_path_p.with_suffix(".debug_refilter_first_page.json")
                        debug_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(debug_path, "w", encoding="utf-8") as f:
                            json.dump(page, f, ensure_ascii=False, indent=2)
                        console.print(f"[dim]Debug: raw first page → {debug_path}[/]")
                    results = page.get("batch_results") or page.get("results") or page.get("data") or []
                    for res in results:
                        rid = res.get("batch_request_id", "")
                        if rid in fetched_ids:
                            continue
                        fetched_ids.add(rid)
                        if res.get("error") or res.get("error_message"):
                            all_failed.append({"batch_request_id": rid, "error": res.get("error_message", res.get("error"))})
                            continue
                        content = _extract_content_from_batch_result(res)
                        all_succeeded.append({"batch_request_id": rid, "content": content})
                    pagination_token = page.get("pagination_token")
                    if not pagination_token:
                        break
                new_succeeded = all_succeeded[count_before:]
                if new_succeeded:
                    chunk_rows = []
                    for s in new_succeeded:
                        rid = s["batch_request_id"]
                        try:
                            idx = int(rid)
                            name = chunk_items[idx]["name"]
                        except (ValueError, IndexError):
                            name = rid
                        score = extract_score(s["content"])
                        chunk_rows.append({
                            "name": name,
                            "score": score,
                            "updated_at": datetime.now().isoformat(),
                        })
                    chunk_base = ((global_offset + count_before) // 1000) * 1000
                    chunk_path = chunk_dir / f"{chunk_base}.csv"
                    save_csv_append(str(chunk_path), chunk_rows, flush_after_write=True)
                    final_so_far.extend(chunk_rows)
                    save_json(str(output_path_p), final_so_far, flush_after_write=True)
                    save_csv(str(csv_path_full), final_so_far, flush_after_write=True)
                    console.print(f"  [dim]→ {chunk_path} (+{len(chunk_rows)} rows) + full [{len(final_so_far)}] flushed[/]")

            console.print(f"  [{poll_count + 1}/{max_polls_before_next}] {num_success + num_error}/{num_requests} complete, {num_pending} pending")
            if num_pending == 0:
                batch_done = True
                break
            poll_count += 1
            time.sleep(10)

        if batch_done:
            _append_failed_rows(chunk_items, all_failed, global_offset, chunk_dir, final_so_far, str(output_path_p), str(csv_path_full))
            total_succeeded += len(all_succeeded)
            total_failed.extend(all_failed)
            if all_failed:
                console.print(f"  [red]Failed this batch: {len(all_failed)} (score=NaN)[/]")
            batch_info = get_batch_status(batch_id, API_KEY)
            ticks = batch_info.get("cost_breakdown", {}).get("total_cost_usd_ticks")
            if ticks is not None:
                total_cost_ticks += ticks
        else:
            incomplete_batches.append((batch_id, chunk_items, global_offset, all_succeeded, all_failed, len(all_succeeded)))
            console.print(f"  [yellow]Still {num_pending} pending → defer to drain, next batch[/]")

    # Drain incomplete batches
    if incomplete_batches:
        console.print(f"\n[bold]Draining {len(incomplete_batches)} incomplete batch(es)...[/]")
        drain_poll = 0
        while incomplete_batches and drain_poll < max_drain_polls:
            still_incomplete = []
            for (batch_id, chunk_items, global_offset, all_succeeded, all_failed, count_flushed) in incomplete_batches:
                batch = get_batch_status(batch_id, API_KEY)
                state = batch.get("state", {})
                num_pending = state.get("num_pending", 0)
                num_success = state.get("num_success", 0)
                num_error = state.get("num_error", 0)
                if num_success + num_error > 0:
                    fetched_ids = {s["batch_request_id"] for s in all_succeeded}
                    fetched_ids.update(f["batch_request_id"] for f in all_failed)
                    pagination_token = None
                    while True:
                        page = list_batch_results(batch_id, API_KEY, page_size=add_batch_size, pagination_token=pagination_token)
                        results = page.get("batch_results") or page.get("results") or page.get("data") or []
                        for res in results:
                            rid = res.get("batch_request_id", "")
                            if rid in fetched_ids:
                                continue
                            fetched_ids.add(rid)
                            if res.get("error") or res.get("error_message"):
                                all_failed.append({"batch_request_id": rid, "error": res.get("error_message", res.get("error"))})
                                continue
                            content = _extract_content_from_batch_result(res)
                            all_succeeded.append({"batch_request_id": rid, "content": content})
                        pagination_token = page.get("pagination_token")
                        if not pagination_token:
                            break
                    new_succeeded = all_succeeded[count_flushed:]
                    if new_succeeded:
                        chunk_rows = []
                        for s in new_succeeded:
                            rid = s["batch_request_id"]
                            try:
                                idx = int(rid)
                                name = chunk_items[idx]["name"]
                            except (ValueError, IndexError):
                                name = rid
                            score = extract_score(s["content"])
                            chunk_rows.append({
                                "name": name,
                                "score": score,
                                "updated_at": datetime.now().isoformat(),
                            })
                        chunk_base = ((global_offset + count_flushed) // 1000) * 1000
                        chunk_path = chunk_dir / f"{chunk_base}.csv"
                        save_csv_append(str(chunk_path), chunk_rows, flush_after_write=True)
                        final_so_far.extend(chunk_rows)
                        save_json(str(output_path_p), final_so_far, flush_after_write=True)
                        save_csv(str(csv_path_full), final_so_far, flush_after_write=True)
                        count_flushed = len(all_succeeded)
                if num_pending == 0:
                    _append_failed_rows(chunk_items, all_failed, global_offset, chunk_dir, final_so_far, str(output_path_p), str(csv_path_full))
                    total_succeeded += len(all_succeeded)
                    total_failed.extend(all_failed)
                    ticks = get_batch_status(batch_id, API_KEY).get("cost_breakdown", {}).get("total_cost_usd_ticks")
                    if ticks is not None:
                        total_cost_ticks += ticks
                    console.print(f"  [dim]Drained {batch_id[:20]}... ({len(all_succeeded)} ok, {len(all_failed)} NaN)[/]")
                else:
                    still_incomplete.append((batch_id, chunk_items, global_offset, all_succeeded, all_failed, count_flushed))
            incomplete_batches = still_incomplete
            if not incomplete_batches:
                break
            drain_poll += 1
            time.sleep(10)
        if incomplete_batches:
            console.print(f"[yellow]Still {len(incomplete_batches)} batch(es) incomplete; partial results saved.[/]")

    console.print(f"\n[bold green]Refilter done. [cyan]{total_succeeded}[/] results → [cyan]{output_path_p}[/], [cyan]{csv_path_full}[/][/]")
    if total_failed:
        console.print(f"[bold red]Total failed: {len(total_failed)}[/]")
    if total_cost_ticks:
        total_cost_usd = total_cost_ticks / 1e10
        table = Table(title="Refilter Batch API Cost (50% off)", title_style="bold cyan")
        table.add_column("Total cost (USD)", style="green")
        table.add_row(f"${total_cost_usd:.4f}")
        console.print("\n", table)


if __name__ == "__main__":
    judge_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Grok 4 Batch: re-score ambiguous names with stricter Korean vs Chinese/Japanese prompt")
    parser.add_argument("--make-ambiguous", action="store_true", help="Only build ambiguous.csv from results CSV (0.9<=score<1, second_refilter=False); no API call")
    parser.add_argument("--source-csv", default="results.csv", help="CSV to read for --make-ambiguous (default: results.csv; use path relative to judge dir or absolute)")
    parser.add_argument("--ambiguous-out", default="ambiguous.csv", help="Output path for --make-ambiguous (default: ambiguous.csv)")
    parser.add_argument("--test", action="store_true", help="Process only first 30 names (like judge_authors_grok --test)")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Process only first N names (e.g. --limit 100)")
    parser.add_argument("--input", default="ambiguous.csv", help="Input CSV with 'name' column for refilter (default: ambiguous.csv)")
    parser.add_argument("--output", default="results/results_refilter_grok.json", help="Output JSON path")
    parser.add_argument("--batch-size", type=int, default=ADD_BATCH_SIZE)
    parser.add_argument("--max-per-batch", type=int, default=MAX_NAMES_PER_BATCH)
    parser.add_argument("--max-polls-before-next", type=int, default=MAX_POLLS_BEFORE_NEXT_BATCH)
    parser.add_argument("--max-drain-polls", type=int, default=MAX_DRAIN_POLLS)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.make_ambiguous:
        make_ambiguous_csv(source_csv=args.source_csv, output_csv=args.ambiguous_out, judge_dir=judge_dir)
    else:
        run_refilter(
            input_csv=args.input,
            output_path=args.output,
            test_mode=args.test,
            limit=args.limit,
            add_batch_size=args.batch_size,
            max_names_per_batch=args.max_per_batch,
            max_polls_before_next=args.max_polls_before_next,
            max_drain_polls=args.max_drain_polls,
            debug=args.debug,
        )
