"""
Grok 4 Batch API: score person names by probability of being Korean (0.0–1.0).
Uses Batch API (50% discount). Test mode: 30 names; normal: requests added in batches of 100.
"""
import csv
import os
import re
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# .env path: project root (901_PCCS)
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

console = Console()

# GROK_API_KEYS or XAI_API_KEY
API_KEY = os.getenv("GROK_API_KEYS") or os.getenv("XAI_API_KEY")
BASE_URL = "https://api.x.ai/v1"
MODEL_NAME = "grok-4-1-fast-non-reasoning"

# Number of requests per add call (limit: 100 add calls per 30s)
ADD_BATCH_SIZE = 100
# Max names per batch: send up to this many, wait for all results, then next batch
MAX_NAMES_PER_BATCH = 1000
# After this many polls, if batch still has pending, move to next batch and drain later
MAX_POLLS_BEFORE_NEXT_BATCH = 5
# When draining incomplete batches, stop after this many poll rounds (avoid infinite wait)
MAX_DRAIN_POLLS = 120  # ~20 min at 10s each
# Retry API calls on 5xx/520 (transient server errors)
API_RETRIES = 5
API_RETRY_BACKOFF = 5.0  # seconds, doubled each retry (5, 10, 20, 40, 80)


def _append_failed_rows(chunk_items, all_failed, global_offset, chunk_dir, final_so_far, output_path, csv_path_full):
    """Append one row per failed request (score=NaN) to final_so_far and to the right chunk CSV; then save full files."""
    if not all_failed:
        return
    now = datetime.now().isoformat()
    # Group failed rows by chunk_base so we append per file
    by_chunk = {}
    for f in all_failed:
        rid = f.get("batch_request_id", "")
        try:
            idx = int(rid)
            name = chunk_items[idx]["name"]
        except (ValueError, IndexError):
            name = rid
        row = {"name": name, "score": None, "updated_at": now}
        final_so_far.append(row)
        chunk_base = ((global_offset + int(rid)) // 1000) * 1000 if rid.isdigit() else 0
        by_chunk.setdefault(chunk_base, []).append(row)
    for chunk_base, rows in by_chunk.items():
        chunk_path = chunk_dir / f"{chunk_base}.csv"
        save_csv_append(str(chunk_path), rows, flush_after_write=True)
    save_json(output_path, final_so_far, flush_after_write=True)
    save_csv(str(csv_path_full), final_so_far, flush_after_write=True)

SYSTEM_PROMPT = (
    "You are an expert in Korean onomastics and romanized name patterns. "
    "Evaluate the probability (0.0 to 1.0) that a given name is of Korean origin. "
    "Western, Japanese, or Chinese names must be scored low or 0.0. "
    "Return ONLY the numerical score. Examples: 'Kim Min-su' -> 1.0, 'Jessica' -> 0.0."
)

# Extra Korean names always included in test mode (for score validation)
EXTRA_TEST_NAMES = ["Gwangsun Kim", "Junhyeong Bae", "Jhon Kim"]


def extract_score(content):
    if not content:
        return None
    match = re.search(r"(\d*\.\d+|\d+)", content)
    if match:
        try:
            val = float(match.group(1))
            if 1.0 < val <= 100.0:
                val /= 100.0
            return max(0.0, min(1.0, val))
        except Exception:
            return None
    return None


def _extract_content_from_batch_result(res: dict) -> str:
    """Extract response text from an xAI batch result (batch_result.response.chat_get_completion)."""
    # 1) batch_result -> response -> chat_get_completion (xAI Batch actual structure)
    raw = res.get("batch_result") or res
    raw = raw.get("response") or raw
    raw = raw.get("chat_get_completion") or raw.get("result") or raw

    # 2) Chat Completions: choices[0].message.content
    choices = raw.get("choices", [])
    if choices:
        msg = choices[0].get("message", choices[0])
        if isinstance(msg, dict):
            c = msg.get("content") or msg.get("text") or ""
            if isinstance(c, str) and c.strip():
                return c.strip()
        if isinstance(msg, str):
            return msg.strip()

    # 3) Responses API: output[0].content[0].text
    output = raw.get("output", [])
    if output and isinstance(output[0], dict):
        content = output[0].get("content", [])
        if content and isinstance(content[0], dict):
            text = content[0].get("text") or content[0].get("content") or ""
            if isinstance(text, str) and text.strip():
                return text.strip()

    # 4) Direct fields
    for key in ("content", "text", "output"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v and isinstance(v[0], dict):
            t = v[0].get("text") or v[0].get("content")
            if isinstance(t, str) and t.strip():
                return t.strip()

    # 5) Recursively find first non-empty string in nested dict/list
    def first_string(obj, depth=0):
        if depth > 5:
            return ""
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        if isinstance(obj, dict):
            for k in ("content", "text", "message", "output"):
                s = first_string(obj.get(k), depth + 1)
                if s:
                    return s
            for v in obj.values():
                s = first_string(v, depth + 1)
                if s:
                    return s
        if isinstance(obj, list):
            for x in obj:
                s = first_string(x, depth + 1)
                if s:
                    return s
        return ""

    return first_string(raw)


def load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_json(path, data, flush_after_write=False):
    p = Path(path)
    if p.parent != p:  # path has directory component
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        if flush_after_write:
            f.flush()
            os.fsync(f.fileno())


def save_csv(path, data, flush_after_write=False):
    """Save the same results as CSV (easy to open in Excel/sheets)."""
    if not data:
        return
    p = Path(path)
    if p.parent != p:
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "score", "updated_at"], extrasaction="ignore")
        w.writeheader()
        for row in data:
            out = dict(row)
            if out.get("score") is None:
                out["score"] = "NaN"
            w.writerow(out)
        if flush_after_write:
            f.flush()
            os.fsync(f.fileno())


def save_csv_append(path, data, flush_after_write=True):
    """Append rows to CSV; write header only if file is new. Use for 1000-unit chunk files (e.g. 13000.csv)."""
    if not data:
        return
    p = Path(path)
    if p.parent != p:
        p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.exists()
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "score", "updated_at"], extrasaction="ignore")
        if not exists:
            w.writeheader()
        for row in data:
            out = dict(row)
            if out.get("score") is None:
                out["score"] = "NaN"
            w.writerow(out)
        if flush_after_write:
            f.flush()
            os.fsync(f.fileno())


def create_batch(name: str, api_key: str) -> str:
    r = _request_with_retry(
        "POST",
        f"{BASE_URL}/batches",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"name": name},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["batch_id"]


def add_batch_requests(batch_id: str, items: list[dict], api_key: str) -> None:
    """items: [{"batch_request_id": str, "name": str}, ...]. Uses retry on 5xx/429 so transient errors don't kill the run."""
    batch_requests = []
    for it in items:
        batch_requests.append({
            "batch_request_id": it["batch_request_id"],
            "batch_request": {
                "chat_get_completion": {
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
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


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Retry on 5xx or 429 (transient/rate-limit errors)."""
    last_r = None
    backoff = API_RETRY_BACKOFF
    for attempt in range(API_RETRIES):
        try:
            r = requests.request(method, url, **kwargs)
            if r.status_code < 500 and r.status_code != 429:
                return r
            last_r = r
        except requests.exceptions.RequestException as e:
            if attempt == API_RETRIES - 1:
                raise
            console.print(f"  [dim]Retry in {backoff:.0f}s ({e})[/]")
        if attempt < API_RETRIES - 1:
            if last_r is not None:
                console.print(f"  [dim]{last_r.status_code} error, retry in {backoff:.0f}s[/]")
            time.sleep(backoff)
            backoff *= 2
    if last_r is not None:
        last_r.raise_for_status()
    return last_r


def get_batch_status(batch_id: str, api_key: str) -> dict:
    r = _request_with_retry(
        "GET",
        f"{BASE_URL}/batches/{batch_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def list_batch_results(batch_id: str, api_key: str, page_size: int = 100, pagination_token: str | None = None) -> dict:
    params = {"page_size": page_size}
    if pagination_token:
        params["pagination_token"] = pagination_token

    r = _request_with_retry(
        "GET",
        f"{BASE_URL}/batches/{batch_id}/results",
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def run(
    input_path: str = "names.json",
    output_path: str = "results/results_grok.json",
    test_mode: bool = False,
    add_batch_size: int = ADD_BATCH_SIZE,
    max_names_per_batch: int = MAX_NAMES_PER_BATCH,
    max_polls_before_next: int = MAX_POLLS_BEFORE_NEXT_BATCH,
    max_drain_polls: int = MAX_DRAIN_POLLS,
    debug: bool = False,
):
    if not API_KEY:
        console.print("[bold red]Missing API key. Set GROK_API_KEYS (or XAI_API_KEY) in .env[/]")
        return

    with console.status("[bold yellow]Loading and filtering..."):
        all_data = load_json(input_path)
        already_done = load_json(output_path)
        # Exclude names that already appear in results (including score=NaN so we don't duplicate; NaN can be post-processed)
        processed_names = {item["name"] for item in already_done}
        target_items = [item for item in all_data if item["name"] not in processed_names]

    num_skipped = len(processed_names)
    if num_skipped:
        console.print(f"[dim]Already in results (skipped): {num_skipped:,} names[/]")

    if test_mode:
        target_items = target_items[:30]
        existing_names = {item["name"] for item in target_items}
        for name in EXTRA_TEST_NAMES:
            if name not in existing_names:
                target_items.append({"name": name})
                existing_names.add(name)
        console.print(f"[bold magenta]Test mode: 30 names + {len(EXTRA_TEST_NAMES)} extra Korean names → {len(target_items)} total.[/]")

    total = len(target_items)
    if total == 0:
        console.print("[bold green]✔ All names already processed.[/]")
        return

    console.print(f"[cyan]Input:[/] {len(all_data):,} | [green]To process (API):[/] {total:,}")

    # Split into batches of max N: send N → wait for all → then next N
    batches_of_names = [
        target_items[i : i + max_names_per_batch]
        for i in range(0, len(target_items), max_names_per_batch)
    ]
    console.print(f"[bold]Batches:[/] {len(batches_of_names)} (max {max_names_per_batch} names each)")

    final_so_far = list(already_done)
    results_dir = Path(output_path).parent
    chunk_dir = results_dir / "tmp"  # incremental chunk CSVs (0.csv, 1000.csv, ...)
    csv_path_full = Path(output_path).with_suffix(".csv")
    total_failed = []
    total_cost_ticks = 0
    total_succeeded = 0
    # Batches that didn't complete within max_polls_before_next; we drain them after all chunks sent
    incomplete_batches = []  # list of (batch_id, chunk_items, global_offset, all_succeeded, all_failed, count_flushed)

    for batch_no, chunk_items in enumerate(batches_of_names):
        n_chunk = len(chunk_items)
        console.print(f"\n[bold cyan]——— Batch {batch_no + 1}/{len(batches_of_names)} ({n_chunk} names) ———[/]")

        # Create batch
        batch_name = f"korean_name_probability_{batch_no + 1}" + ("_test" if test_mode else "")
        batch_id = create_batch(batch_name, API_KEY)
        console.print(f"  batch_id: [cyan]{batch_id}[/]")

        # Add requests in sub-chunks of add_batch_size (rate limit: 100 add calls per 30s)
        items_for_add = [
            {"batch_request_id": str(i), "name": chunk_items[i]["name"]}
            for i in range(n_chunk)
        ]
        for start in range(0, len(items_for_add), add_batch_size):
            chunk = items_for_add[start : start + add_batch_size]
            add_batch_requests(batch_id, chunk, API_KEY)
            console.print(f"  Added {start + 1}–{start + len(chunk)}/{n_chunk}")
            if start + add_batch_size < len(items_for_add):
                time.sleep(0.5)

        # Poll up to max_polls_before_next; if still pending, defer to drain phase and move to next batch
        console.print(f"[bold]Waiting for batch (max {max_polls_before_next} polls, then next batch)...[/]")
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
            # Fetch any available results (partial OK: e.g. 900 done → fetch and flush 900)
            if completed > 0:
                count_before = len(all_succeeded)
                fetched_ids = {s["batch_request_id"] for s in all_succeeded}
                fetched_ids.update(f["batch_request_id"] for f in all_failed)
                pagination_token = None
                while True:
                    page = list_batch_results(batch_id, API_KEY, page_size=add_batch_size, pagination_token=pagination_token)
                    if debug and batch_no == 0 and len(fetched_ids) == 0 and pagination_token is None:
                        debug_path = Path(output_path).with_suffix(".debug_first_page.json")
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
                # Flush newly fetched rows only
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
                    save_json(output_path, final_so_far, flush_after_write=True)
                    save_csv(str(csv_path_full), final_so_far, flush_after_write=True)
                    console.print(f"  [dim]→ {chunk_path} (+{len(chunk_rows)} rows) + full [{len(final_so_far)}] flushed[/]")

            console.print(f"  [{poll_count + 1}/{max_polls_before_next}] {num_success + num_error}/{num_requests} complete, {num_pending} pending")
            if num_pending == 0:
                batch_done = True
                break
            poll_count += 1
            time.sleep(10)

        if batch_done:
            _append_failed_rows(chunk_items, all_failed, global_offset, chunk_dir, final_so_far, output_path, str(csv_path_full))
            total_succeeded += len(all_succeeded)
            total_failed.extend(all_failed)
            if all_failed:
                console.print(f"  [red]Failed this batch: {len(all_failed)} (score=NaN in results)[/]")
            batch_info = get_batch_status(batch_id, API_KEY)
            cost_breakdown = batch_info.get("cost_breakdown", {})
            ticks = cost_breakdown.get("total_cost_usd_ticks")
            if ticks is not None:
                total_cost_ticks += ticks
        else:
            # Defer to drain phase; we've already flushed partial results
            incomplete_batches.append((batch_id, chunk_items, global_offset, all_succeeded, all_failed, len(all_succeeded)))
            console.print(f"  [yellow]Still {num_pending} pending after {max_polls_before_next} polls → defer to drain phase, moving to next batch[/]")

    # Drain incomplete batches: poll until complete (or max_drain_polls), merge results
    if incomplete_batches:
        console.print(f"\n[bold]Draining {len(incomplete_batches)} incomplete batch(es) (max {max_drain_polls} poll rounds)...[/]")
        drain_poll = 0
        while incomplete_batches and drain_poll < max_drain_polls:
            still_incomplete = []
            for (batch_id, chunk_items, global_offset, all_succeeded, all_failed, count_flushed) in incomplete_batches:
                batch = get_batch_status(batch_id, API_KEY)
                state = batch.get("state", {})
                num_pending = state.get("num_pending", 0)
                num_success = state.get("num_success", 0)
                num_error = state.get("num_error", 0)
                num_requests = state.get("num_requests", 0)
                completed = num_success + num_error
                if completed > 0:
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
                        save_json(output_path, final_so_far, flush_after_write=True)
                        save_csv(str(csv_path_full), final_so_far, flush_after_write=True)
                        count_flushed = len(all_succeeded)
                if num_pending == 0:
                    _append_failed_rows(chunk_items, all_failed, global_offset, chunk_dir, final_so_far, output_path, str(csv_path_full))
                    total_succeeded += len(all_succeeded)
                    total_failed.extend(all_failed)
                    batch_info = get_batch_status(batch_id, API_KEY)
                    ticks = batch_info.get("cost_breakdown", {}).get("total_cost_usd_ticks")
                    if ticks is not None:
                        total_cost_ticks += ticks
                    console.print(f"  [dim]Drained batch [cyan]{batch_id[:20]}...[/] ({len(all_succeeded)} ok, {len(all_failed)} NaN)[/]")
                else:
                    still_incomplete.append((batch_id, chunk_items, global_offset, all_succeeded, all_failed, count_flushed))
            incomplete_batches = still_incomplete
            if not incomplete_batches:
                break
            drain_poll += 1
            time.sleep(10)
        if incomplete_batches:
            console.print(f"[yellow]Still {len(incomplete_batches)} batch(es) incomplete after drain; partial results saved.[/]")

    console.print(f"\n[bold green]Done. [cyan]{total_succeeded}[/] results → [cyan]{output_path}[/], [cyan]{csv_path_full}[/][/]")
    if total_failed:
        console.print(f"[bold red]Total failed: {len(total_failed)}[/]")
        for f in total_failed[:5]:
            console.print(f"  {f}")
        if len(total_failed) > 5:
            console.print(f"  ... and {len(total_failed) - 5} more")
    if total_cost_ticks:
        total_cost_usd = total_cost_ticks / 1e10
        table = Table(title="Grok Batch API Cost (50% off)", title_style="bold cyan")
        table.add_column("Total cost (USD)", style="green")
        table.add_row(f"${total_cost_usd:.4f}")
        console.print("\n", table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grok 4 Batch API: Korean name probability")
    parser.add_argument("--test", action="store_true", help="Test mode: process only 30 names")
    parser.add_argument("--input", default="names.json", help="Input JSON path")
    parser.add_argument("--output", default="results/results_grok.json", help="Output JSON path (default: results/results_grok.json)")
    parser.add_argument("--batch-size", type=int, default=ADD_BATCH_SIZE, help="Requests per add call (default 100)")
    parser.add_argument("--max-per-batch", type=int, default=MAX_NAMES_PER_BATCH, help=f"Max names per batch (default {MAX_NAMES_PER_BATCH})")
    parser.add_argument("--max-polls-before-next", type=int, default=MAX_POLLS_BEFORE_NEXT_BATCH, help=f"Polls per batch before moving on (default {MAX_POLLS_BEFORE_NEXT_BATCH})")
    parser.add_argument("--max-drain-polls", type=int, default=MAX_DRAIN_POLLS, help=f"Max poll rounds when draining incomplete batches (default {MAX_DRAIN_POLLS})")
    parser.add_argument("--debug", action="store_true", help="Save raw first page of results to .debug_first_page.json")
    args = parser.parse_args()

    run(
        input_path=args.input,
        output_path=args.output,
        test_mode=args.test,
        add_batch_size=args.batch_size,
        max_names_per_batch=args.max_per_batch,
        max_polls_before_next=args.max_polls_before_next,
        max_drain_polls=args.max_drain_polls,
        debug=args.debug,
    )
