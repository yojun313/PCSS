import json
import os
from datetime import datetime
from pymongo import MongoClient
from rich.console import Console
from rich.progress import Progress
from dotenv import load_dotenv
from app.db import name_col, mongo_db

# 설정 로드
load_dotenv()
console = Console()

rejudge_col = mongo_db["llm_names"]
llm_col = mongo_db["name_score_deepseek_3"]


def export_score_discrepancies():
    # 1. 원본 데이터(llm_names) 로드 (이름: 점수 매핑)
    with console.status(
        "[bold cyan]Loading original scores from 'llm_names'..."
    ) as status:
        # 메모리 효율을 위해 이름과 점수만 가져옴
        original_data = {
            doc["name"]: doc.get("score")
            for doc in llm_col.find({}, {"name": 1, "score": 1, "_id": 0})
        }
        console.print(
            f"[green]✔ Loaded {len(original_data):,} names from llm_names.[/]"
        )

    discrepancies = []

    # 2. 재판단 데이터(name_score_deepseek_3)와 비교
    with Progress() as progress:
        task = progress.add_task(
            "[yellow]Comparing scores...", total=rejudge_col.count_documents({})
        )

        # rejudged_names를 하나씩 순회
        cursor = rejudge_col.find({}, {"name": 1, "score": 1, "_id": 0})

        for doc in cursor:
            name = doc["name"]
            rejudged_score = doc.get("score")
            original_score = original_data.get(name)

            # 두 컬렉션에 모두 존재하고, 점수가 다른 경우만 추출
            # (소수점 부동소수점 오차를 방지하기 위해 round 처리 권장)
            if original_score is not None and rejudged_score is not None:
                if round(original_score, 4) != round(rejudged_score, 4):
                    discrepancies.append(
                        {
                            "name": name,
                            "deepseek": original_score,
                            "grok": rejudged_score,
                            "diff": round(rejudged_score - original_score, 4),
                        }
                    )

            progress.advance(task)

    # 3. JSON 파일로 저장
    output_filename = (
        f"score_discrepancies_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )

    try:
        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(discrepancies, f, ensure_ascii=False, indent=4)

        console.print("\n" + "─" * 50)
        console.print(f"[bold green]Analysis Complete![/]")
        console.print(f"Total discrepancies found: [bold red]{len(discrepancies):,}[/]")
        console.print(f"File saved to: [bold blue]{output_filename}[/]")
        console.print("─" * 50)

    except Exception as e:
        console.print(f"[bold red]Error saving JSON:[/] {e}")


if __name__ == "__main__":
    # DB 연결 확인 후 실행
    try:
        export_score_discrepancies()
    except Exception as e:
        console.print(f"[bold red]Could not connect to MongoDB:[/] {e}")
