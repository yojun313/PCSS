import os
import re
import asyncio
import aiohttp
from datetime import datetime
from typing import List
from dotenv import load_dotenv
from pymongo import UpdateOne
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
from app.db import name_col, mongo_db

load_dotenv()
console = Console()

rejudged_col = mongo_db["rejudged_names"]

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL_NAME = "deepseek-chat"

PRICE_HIT = 0.028 / 1_000_000
PRICE_MISS = 0.28 / 1_000_000
PRICE_OUT = 0.42 / 1_000_000


class DeepSeekRejudgeProcessor:
    def __init__(self, max_concurrency=100, batch_update_size=100):
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.batch_update_size = batch_update_size
        self.total_usage = {"hit": 0, "miss": 0, "out": 0}

        self.system_prompt = (
            "You are an expert in Korean onomastics and romanized name patterns. "
            "Evaluate the probability (0.0 to 1.0) that a given name is of Korean origin. "
            "Criteria: 1-syllable surname and 2-syllable given names are typical. "
            "Western, Japanese, or Chinese names must be scored low or 0.0. "
            "Return ONLY the numerical score. Examples: 'Kim Min-su' -> 1.0, 'Jessica' -> 0.0."
        )

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

    async def process_name(self, session, name):  # progress, task_id 인자 제거
        async with self.semaphore:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"Name: '{name}'"},
                ],
                "temperature": 0.0,
            }

            try:
                async with session.post(
                    BASE_URL, json=payload, headers=headers, timeout=60
                ) as resp:
                    if resp.status != 200:
                        return None
                    result = await resp.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    usage = result.get("usage", {})
                    score = self.extract_score(content)

                    return {
                        "name": name,
                        "score": score,
                        "hit": usage.get("prompt_cache_hit_tokens", 0),
                        "miss": usage.get("prompt_cache_miss_tokens", 0),
                        "out": usage.get("completion_tokens", 0),
                    }
            except Exception as e:
                console.print(f"[red]Error processing {name}: {e}[/]")
                return None

    async def run_rejudge(self):
        with console.status("[bold yellow]Filtering names for re-judgment..."):
            # 1. 이미 처리된(판단 완료된) 이름들 가져오기
            processed_docs = list(rejudged_col.find({}, {"_id": 0, "name": 1}))
            processed_names = {doc["name"] for doc in processed_docs}  # set으로 변환

            # 2. 전체 대상 이름 가져오기
            all_docs = list(name_col.find({}, {"_id": 0, "name": 1}))
            all_names = [doc["name"] for doc in all_docs]

            # 3. 차집합 계산 (이미 처리된 건 제외)
            target_names = [name for name in all_names if name not in processed_names]

        total_names = len(target_names)
        if total_names == 0:
            console.print(
                "[bold green]All names are already re-judged! Nothing to do.[/]"
            )
            return

        console.print(
            f"[cyan]Total:[/][white] {len(all_names)}[/] | [green]Remaining:[/][white] {total_names}[/]"
        )

        db_ops = []
        async with aiohttp.ClientSession() as session:
            # Progress 설정을 좀 더 가독성 있게 조정
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold magenta]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task_id = progress.add_task("Re-judging Names", total=total_names)
                # 테스크 생성 시 progress 인자 제거
                tasks = [self.process_name(session, name) for name in target_names]

                for completed_task in asyncio.as_completed(tasks):
                    res = await completed_task

                    # 결과와 상관없이 진행률 업데이트 (실패한 건도 진행된 것으로 간주)
                    progress.advance(task_id)

                    if res and res["score"] is not None:
                        self.total_usage["hit"] += res["hit"]
                        self.total_usage["miss"] += res["miss"]
                        self.total_usage["out"] += res["out"]

                        db_ops.append(
                            UpdateOne(
                                {"name": res["name"]},
                                {
                                    "$set": {
                                        "score": res["score"],
                                        "updated_at": datetime.now(),
                                    },
                                    "$setOnInsert": {"created_at": datetime.now()},
                                },
                                upsert=True,
                            )
                        )

                        if len(db_ops) >= self.batch_update_size:
                            rejudged_col.bulk_write(db_ops)
                            db_ops = []

                # 남은 데이터 처리
                if db_ops:
                    rejudged_col.bulk_write(db_ops)

        self.display_summary()

    def display_summary(self):
        total_cost = (
            (self.total_usage["hit"] * PRICE_HIT)
            + (self.total_usage["miss"] * PRICE_MISS)
            + (self.total_usage["out"] * PRICE_OUT)
        )
        table = Table(title="Re-judge Usage Summary", title_style="bold cyan")
        table.add_column("Type")
        table.add_column("Tokens", justify="right")
        table.add_column("Cost (USD)", style="green")
        table.add_row(
            "Cache Hit",
            f"{self.total_usage['hit']:,}",
            f"${self.total_usage['hit'] * PRICE_HIT:.6f}",
        )
        table.add_row(
            "Cache Miss",
            f"{self.total_usage['miss']:,}",
            f"${self.total_usage['miss'] * PRICE_MISS:.6f}",
        )
        table.add_row(
            "Output",
            f"{self.total_usage['out']:,}",
            f"${self.total_usage['out'] * PRICE_OUT:.6f}",
        )
        table.add_section()
        table.add_row("[bold]Total Cost[/]", "-", f"[bold]${total_cost:.4f}[/]")
        console.print("\n", table)


if __name__ == "__main__":
    processor = DeepSeekRejudgeProcessor(max_concurrency=30, batch_update_size=30)
    try:
        asyncio.run(processor.run_rejudge())
    except KeyboardInterrupt:
        console.print("\n[bold red]Stopped by user.[/]")
