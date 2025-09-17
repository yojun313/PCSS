#!/usr/bin/env python3
import os
import sys
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import quote_plus

from pymongo import MongoClient
from dotenv import load_dotenv

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import track
from rich import box

load_dotenv()
console = Console()

# ---- 환경변수 ----
MONGODB_URI   = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME       = os.getenv("DB_NAME", "pcss")
LOG_COLLECTION= os.getenv("LOG_COLLECTION", "logs")  # 날짜별 문서에 logs 배열이 있는 컬렉션명
REPORT_PATH   = os.getenv("REPORT_PATH", "pcss_log_report.html")

def norm_ip(ip: str) -> str:
    # ::ffff:10.0.0.1 형태 → 10.0.0.1 로 정규화
    if ip and ip.startswith("::ffff:"):
        return ip.split("::ffff:")[-1]
    return ip or "-"

def safe_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def main():
    # ---- Mongo 연결 ----
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        col = db[LOG_COLLECTION]
    except Exception as e:
        console.print(f"[red]MongoDB 연결 실패:[/red] {e}")
        sys.exit(1)

    # ---- 데이터 로드 ----
    docs = list(col.find({}, {"_id": 0, "date": 1, "logs": 1}))
    if not docs:
        console.print("[yellow]데이터가 없습니다.[/yellow]")
        return

    # ---- 통계 변수 ----
    total_requests = 0
    by_date   = Counter()
    by_path   = Counter()
    by_ip     = Counter()
    by_option = Counter()
    by_count_option = Counter()
    by_uncertainty  = Counter()
    by_conf  = Counter()
    by_hour_utc = Counter()

    first_ts = None
    last_ts  = None

    # ---- 파싱 ----
    for doc in track(docs, description="Parsing logs..."):
        doc_date = doc.get("date", "-")
        logs = doc.get("logs", [])
        by_date[doc_date] += len(logs)
        total_requests += len(logs)

        for item in logs:
            ip   = norm_ip(item.get("ip"))
            path = item.get("path", "-")
            data = item.get("data", {})

            # 시간 파싱 (UTC ISO)
            tstr = item.get("time")
            try:
                ts = datetime.fromisoformat(tstr.replace("Z", "+00:00"))
            except Exception:
                ts = None

            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                by_hour_utc[ts.strftime("%Y-%m-%d %H:00")] += 1

            by_ip[ip] += 1
            by_path[path] += 1

            option = str(safe_get(data, "option", default="-"))
            by_option[option] += 1

            cnt_opt = str(safe_get(data, "CountOption", default="-")).lower()
            by_count_option[cnt_opt] += 1

            uncertainty = str(safe_get(data, "uncertainty", default="-"))
            by_uncertainty[uncertainty] += 1

            confs = safe_get(data, "selectedConferences", default=[])
            if isinstance(confs, list):
                for c in confs:
                    by_conf[str(c)] += 1

    # ---- 터미널 출력 (Rich) ----
    console.print(Panel.fit(f"[bold magenta]PCSS Log Report[/bold magenta]\n[white]{DB_NAME}.{LOG_COLLECTION}[/white]", border_style="magenta"))

    summary = Table(show_header=False, box=box.SIMPLE_HEAVY)
    summary.add_row("총 요청 수", f"{total_requests:,}")
    summary.add_row("문서(날짜) 수", f"{len(docs):,}")
    if first_ts and last_ts:
        summary.add_row("기간 (UTC)", f"{first_ts.isoformat()}  ~  {last_ts.isoformat()}")
    console.print(Panel(summary, title="요약", border_style="cyan"))

    def print_top(title, counter, topn=10):
        tbl = Table(title=title, box=box.MINIMAL_DOUBLE_HEAD)
        tbl.add_column("Rank", justify="right")
        tbl.add_column("Key", overflow="fold")
        tbl.add_column("Count", justify="right")
        for i, (k, v) in enumerate(counter.most_common(topn), start=1):
            tbl.add_row(str(i), str(k), f"{v:,}")
        console.print(tbl)

    print_top("요청수 (날짜별)", by_date, 10)
    print_top("요청수 (경로별)", by_path, 10)
    print_top("요청수 (IP별)", by_ip, 10)
    print_top("옵션(option) 분포", by_option, 10)
    print_top("CountOption 분포", by_count_option, 10)
    print_top("uncertainty 분포", by_uncertainty, 10)
    print_top("선택된 컨퍼런스", by_conf, 15)

    # 시간대 히스토그램(UTC 시간별)
    tbl_hour = Table(title="시간대별 요청수 (UTC, Hour)", box=box.SIMPLE)
    tbl_hour.add_column("Hour", overflow="fold")
    tbl_hour.add_column("Count", justify="right")
    for hour_key, cnt in sorted(by_hour_utc.items()):
        tbl_hour.add_row(hour_key, str(cnt))
    console.print(tbl_hour)

    # ---- HTML 리포트 생성 ----
    def counter_to_html_rows(counter, title, topn=20):
        rows = []
        rows.append(f"<h3>{title}</h3>")
        rows.append("<table><thead><tr><th>#</th><th>Key</th><th>Count</th></tr></thead><tbody>")
        for i, (k, v) in enumerate(counter.most_common(topn), start=1):
            rows.append(f"<tr><td>{i}</td><td>{k}</td><td>{v}</td></tr>")
        rows.append("</tbody></table>")
        return "\n".join(rows)

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>PCSS Log Report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; background:#f9f9fb; color:#222; }}
  h1 {{ color:#c00150; }}
  .card {{ background:#fff; border:1px solid #e8e8ef; border-radius:12px; padding:16px 20px; margin:16px 0; box-shadow:0 4px 14px rgba(0,0,0,.04); }}
  table {{ width:100%; border-collapse: collapse; margin:8px 0 24px; }}
  th, td {{ padding:10px 12px; border-bottom:1px solid #eee; text-align:left; }}
  th {{ background:#fafafd; color:#444; }}
  .muted {{ color:#666; }}
  code {{ background:#f0f0f5; padding:2px 6px; border-radius:6px; }}
</style>
</head>
<body>
  <h1>PCSS Log Report</h1>
  <div class="card">
    <p><b>DB</b>: <code>{DB_NAME}</code> &nbsp; <b>Collection</b>: <code>{LOG_COLLECTION}</code></p>
    <p><b>Total Requests</b>: {total_requests:,}</p>
    <p><b>Documents (days)</b>: {len(docs):,}</p>
    <p class="muted"><b>Range (UTC)</b>: {first_ts.isoformat() if first_ts else '-'} ~ {last_ts.isoformat() if last_ts else '-'}</p>
  </div>

  <div class="card">
    {counter_to_html_rows(by_date, "요청수 (날짜별)", 30)}
    {counter_to_html_rows(by_path, "요청수 (경로별)", 20)}
    {counter_to_html_rows(by_ip, "요청수 (IP별)", 20)}
    {counter_to_html_rows(by_option, "옵션(option) 분포", 10)}
    {counter_to_html_rows(by_count_option, "CountOption 분포", 10)}
    {counter_to_html_rows(by_uncertainty, "uncertainty 분포", 15)}
    {counter_to_html_rows(by_conf, "선택된 컨퍼런스", 40)}
    <h3>시간대별 요청수 (UTC, hour)</h3>
    <table>
      <thead><tr><th>Hour (UTC)</th><th>Count</th></tr></thead>
      <tbody>
        {"".join([f"<tr><td>{h}</td><td>{c}</td></tr>" for h,c in sorted(by_hour_utc.items())])}
      </tbody>
    </table>
  </div>

  <p class="muted">Generated at {datetime.utcnow().isoformat()}Z</p>
</body>
</html>
"""

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    console.print(Panel.fit(f"[green]HTML 저장 완료[/green] → {REPORT_PATH}", border_style="green"))

if __name__ == "__main__":
    main()
