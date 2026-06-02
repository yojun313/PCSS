import os
import re
import asyncio
import aiohttp
from datetime import datetime
from typing import List
from dotenv import load_dotenv
from pymongo import UpdateOne, ASCENDING
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from app.db import name_col, papers_col

load_dotenv()
console = Console()

XAI_API_KEY = os.getenv("GROK_API_KEYS") or os.getenv("XAI_API_KEY")
BASE_URL = "https://api.x.ai/v1/chat/completions"

MODEL_MAIN = "grok-4-1-fast-non-reasoning"
MODEL_REFILTER = "grok-4-1-fast-reasoning"

PROMPT_MAIN = (
    "You are an expert in Korean onomastics and romanized name patterns. "
    "Evaluate the probability (0.0 to 1.0) that a given name is of Korean origin. "
    "Western, Japanese, or Chinese names must be scored low or 0.0. "
    "Return ONLY the numerical score. Examples: 'Kim Min-su' -> 1.0, 'Jessica' -> 0.0."
)

PROMPT_REFILTER = (
    "You classify romanized names by the probability of being South Korean (0.0-1.0). Output a single number only.\n\n"
    "Context: Korean names usually follow the Revised Romanization (standard) or McCune-Reischauer. Chinese names use Pinyin (Mainland) or Yale/Jyutping (Hong Kong/Taiwan).\n\n"
    "1. High Probability (0.90-1.0) - Clearly Korean\n"
    "- Standard Surnames: Kim, Lee, Park, Choi, Jung (or Jeong), Kang, Cho, Yoon, Jang, Shin, Lim (or Im), Han, Oh, Seo, Kwon, Hwang, Song.\n"
    "- Korean-style Given Names: Two-syllable names (often hyphenated or space-separated) using standard Korean vowel combinations:\n"
    "    - Vowels: 'eo', 'eu', 'ae', 'ui', 'ie'.\n"
    "    - Syllables: -su, -ho, -hee, -young, -hyeon, -jun, -min, -bin, -woo, -seok, -jae, -hun.\n"
    "- Example: Min-su Kim, Jeong-hun Lee, Ji-hye Park.\n\n"
    "2. Ambiguous / Low-Mid (0.30-0.50) - Shared or Non-standard\n"
    "- Surname 'Yi': While 'Yi' can be a traditional Korean spelling for 李(이), it is significantly more common in Chinese (易, 伊) or non-standard. Score 0.4-0.5 max even if the given name looks slightly Korean.\n"
    "- Single-syllable Given Names: Surnames like Kim/Lee/Park with a single-syllable given name (e.g., Jin Kim, Sang Lee) are common in both cultures.\n"
    "- Westernized: 'Michael Kim', 'Jessica Park' - if clearly Korean family name -> 0.90; if any doubt -> 0.60-0.65. (Do not use 0.80-0.89.)\n\n"
    "3. Very Low Probability (0.0-0.25) - Likely Chinese/Japanese/Other\n"
    "- Non-Korean Syllable Structures (CRITICAL):\n"
    "    - 'Chung' with 'Yi': Names like 'Da-Chung', 'Ka-Ho', 'Wing', 'Siu', 'Wai', 'Hing', 'Fai' are Cantonese/Taiwanese.\n"
    "    - Pinyin Indicators: 'X', 'Q', 'Z' at the start of syllables (e.g., Xiao, Qiang, Zhang, Zhao, Zhu).\n"
    "    - Vowel/Ending Clusters: Names ending in -ng (except common Korean ones like Jung/Sung/Hong) combined with surnames like Yi, Li, Wang, Chen.\n"
    "- 'Yi' + Non-standard Vowels: 'Yi' combined with names containing 'u' used as 'oo' (like 'Chung') or 'a' used in ways inconsistent with Korean phonology.\n"
    "- Example: Da-Chung Yi (0.1), Xiao-Xing Chen (0.0), Yamamoto (0.0).\n\n"
    "Scoring Logic:\n"
    "- If Surname is 'Yi', start at 0.4. If the given name has non-Korean phonetic patterns (e.g., 'Chung', 'Wah'), drop to 0.1-0.2.\n"
    "- If Surname is 'Lee' but given name is Pinyin-style (e.g., 'Liang', 'Wei'), score 0.2.\n"
    "- If Surname is 'Kim/Park' and given name is 2-syllable Korean style, score 0.95+.\n\n"
    "Critical - avoid 0.85-0.89: If you are tempted to score 0.85 or above, pause and reconsider. Then output either:\n"
    "- 0.90-1.0 (clearly Korean: standard surname + clearly Korean given name), or\n"
    "- 0.60-0.69 (ambiguous: could be Korean but not confident - e.g. single-syllable given name, or surname/given name that could be Chinese).\n"
    "Do NOT output 0.80-0.89. Borderline cases -> 0.6x; only clearly Korean -> 0.9x.\n\n"
    "Return ONLY the number."
)


class GrokNameProcessor:
    def __init__(self, max_concurrency=50, batch_update_size=50):
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.batch_update_size = batch_update_size
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def extract_score(self, content):
        if not content:
            return None
        match = re.search(r"(\d*\.\d+|\d+)", content)
        if match:
            try:
                val = float(match.group(1))
                if 1.0 < val <= 100.0:
                    val /= 100.0
                return max(0.0, min(1.0, val))
            except:
                return None
        return None

    async def process_name(self, session, name, is_refilter, progress, task_id):
        async with self.semaphore:
            headers = {
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            }
            model = MODEL_REFILTER if is_refilter else MODEL_MAIN
            sys_prompt = PROMPT_REFILTER if is_refilter else PROMPT_MAIN

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"Name: '{name}'"},
                ],
                "temperature": 0.0,
            }

            for attempt in range(5):
                try:
                    async with session.post(
                        BASE_URL, json=payload, headers=headers, timeout=60
                    ) as resp:
                        if resp.status == 429 or resp.status >= 500:
                            await asyncio.sleep(5 * (2**attempt))
                            continue
                        if resp.status != 200:
                            return None

                        result = await resp.json()
                        content = result["choices"][0]["message"]["content"].strip()
                        usage = result.get("usage", {})
                        score = self.extract_score(content)

                        mode_label = (
                            "[magenta]REFILTER[/]" if is_refilter else "[blue]MAIN[/]"
                        )
                        progress.console.print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] {mode_label} "
                            f"[cyan]{name:<15}[/] | Score: [bold yellow]{score if score is not None else 'FAIL':<5}[/]"
                        )

                        progress.advance(task_id)
                        return {
                            "name": name,
                            "score": score,
                            "is_refilter": is_refilter,
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                        }
                except:
                    await asyncio.sleep(5 * (2**attempt))
            return None

    async def run_processing(self, target_names: List[str], is_refilter=False):
        total_names = len(target_names)
        if total_names == 0:
            console.print("[green]No names to process for this phase.[/]")
            return

        db_ops = []
        async with aiohttp.ClientSession() as session:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("({task.completed}/{task.total})"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                desc = "Refiltering" if is_refilter else "Processing"
                task_id = progress.add_task(desc, total=total_names)
                name_iterator = iter(target_names)
                pending = set()

                try:
                    while len(pending) < self.semaphore._value:
                        try:
                            name = next(name_iterator)
                            pending.add(
                                asyncio.create_task(
                                    self.process_name(
                                        session, name, is_refilter, progress, task_id
                                    )
                                )
                            )
                        except StopIteration:
                            break

                    while pending:
                        done, pending = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_COMPLETED
                        )

                        for task in done:
                            res = await task
                            if res and res["score"] is not None:
                                self.total_usage["prompt_tokens"] += res[
                                    "prompt_tokens"
                                ]
                                self.total_usage["completion_tokens"] += res[
                                    "completion_tokens"
                                ]

                                update_data = {
                                    "score": res["score"],
                                    "updated_at": datetime.now(),
                                }
                                if is_refilter:
                                    update_data["second_refilter"] = True

                                db_ops.append(
                                    UpdateOne(
                                        {"name": res["name"]},
                                        {
                                            "$set": update_data,
                                            "$setOnInsert": {
                                                "created_at": datetime.now()
                                            },
                                        },
                                        upsert=True,
                                    )
                                )

                                if len(db_ops) >= self.batch_update_size:
                                    name_col.bulk_write(db_ops)
                                    db_ops = []

                        while len(pending) < self.semaphore._value:
                            try:
                                name = next(name_iterator)
                                pending.add(
                                    asyncio.create_task(
                                        self.process_name(
                                            session,
                                            name,
                                            is_refilter,
                                            progress,
                                            task_id,
                                        )
                                    )
                                )
                            except StopIteration:
                                break

                except KeyboardInterrupt:
                    console.print(
                        "\n[bold red]Interrupted. Saving current progress...[/]"
                    )
                finally:
                    if db_ops:
                        name_col.bulk_write(db_ops)
                        console.print(
                            f"[bold green]Batch updated {len(db_ops)} records safely.[/]"
                        )

        self.display_summary()

    def display_summary(self):
        table = Table(title="Usage Summary", title_style="bold magenta")
        table.add_column("Type")
        table.add_column("Tokens", justify="right")
        table.add_row("Prompt Tokens", f"{self.total_usage['prompt_tokens']:,}")
        table.add_row("Completion Tokens", f"{self.total_usage['completion_tokens']:,}")
        console.print("\n", table)


def extract_new_names() -> List[str]:
    with console.status("[bold yellow]Optimizing DB query..."):
        name_col.create_index([("name", ASCENDING)])
        papers_col.create_index([("author_names", ASCENDING)])

    with console.status("[bold yellow]Fetching unique names from papers..."):
        pipeline = [{"$unwind": "$author_names"}, {"$group": {"_id": "$author_names"}}]
        raw_author_names = [doc["_id"] for doc in papers_col.aggregate(pipeline)]

    with console.status("[bold yellow]Cleaning and deduplicating..."):
        cleaned_names = {
            re.sub(r"\d+", "", (name or "")).strip()
            for name in raw_author_names
            if name
        }
        cleaned_names.discard("")

    with console.status("[bold yellow]Comparing with existing records..."):
        processed_pipeline = [{"$group": {"_id": "$name"}}]
        processed_names = {doc["_id"] for doc in name_col.aggregate(processed_pipeline)}

    update_targets = list(cleaned_names - processed_names)
    console.print(f"[green]Found {len(update_targets)} new names to process.[/]")
    return update_targets


def get_rejudge_targets(
    threshold: float = 0.7, target_names: List[str] = None
) -> List[str]:
    query = {"score": {"$gte": threshold}, "second_refilter": {"$ne": True}}

    if target_names is not None:
        query["name"] = {"$in": target_names}

    with console.status(
        f"[bold yellow]Filtering names with score >= {threshold} for refilter..."
    ):
        targets = [doc["name"] for doc in name_col.find(query, {"_id": 0, "name": 1})]
    console.print(f"[green]Found {len(targets)} names for rejudging.[/]")
    return targets


async def main(automatic=False):
    if not XAI_API_KEY:
        console.print(
            "[bold red]Missing API key. Set GROK_API_KEYS or XAI_API_KEY in .env[/]"
        )
        return

    processor = GrokNameProcessor(max_concurrency=50, batch_update_size=50)

    if automatic:
        targets = extract_new_names()
        await processor.run_processing(targets, is_refilter=False)

        refilter_targets = get_rejudge_targets(0.7, target_names=targets)
        await processor.run_processing(refilter_targets, is_refilter=True)
        return

    choice = input(
        "1: Main Extraction & Refilter Process\n2: Refilter (High Score Only)\nSelect (1/2): "
    )
    if choice == "1":
        targets = extract_new_names()
        await processor.run_processing(targets, is_refilter=False)

        console.print("\n[bold cyan]Starting Refilter Phase...[/]")
        refilter_targets = get_rejudge_targets(0.7, target_names=targets)
        await processor.run_processing(refilter_targets, is_refilter=True)

    elif choice == "2":
        t_input = input("Threshold (default 0.7): ")
        threshold = float(t_input) if t_input else 0.7
        targets = get_rejudge_targets(threshold)
        await processor.run_processing(targets, is_refilter=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
