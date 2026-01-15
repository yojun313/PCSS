import argparse
import asyncio
import json
from typing import Dict, Optional, Tuple
import httpx


def _join_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def parse_sse_event(block: str) -> Optional[Tuple[str, str]]:
    """
    SSE block example:
      event: message
      data: {...}

    Returns (event_name, data_str) or None if invalid.
    """
    event_name = "message"
    data_lines = []

    for line in block.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    if not data_lines:
        return None

    data_str = "\n".join(data_lines)
    return event_name, data_str


async def start_job(client: httpx.AsyncClient, base_url: str, payload: Dict) -> Dict:
    url = _join_url(base_url, "/api/search/start")
    r = await client.post(url, json=payload)
    r.raise_for_status()
    return r.json()


async def stream_events(client: httpx.AsyncClient, events_url: str) -> Dict:
    """
    Connect to SSE and stream until event: done/error.
    Returns last terminal event payload (dict).
    """
    terminal: Dict = {"event": None}

    # NOTE: httpx will keep the connection open; we read line by line.
    async with client.stream("GET", events_url, headers={"Accept": "text/event-stream"}) as r:
        r.raise_for_status()

        buffer_lines = []
        async for raw_line in r.aiter_lines():
            # SSE blocks are separated by blank line
            if raw_line == "":
                block = "\n".join(buffer_lines).strip()
                buffer_lines = []
                if not block:
                    continue

                parsed = parse_sse_event(block)
                if not parsed:
                    continue

                event_name, data_str = parsed
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    # Some servers may send non-JSON data; print raw.
                    print(f"[SSE:{event_name}] {data_str}")
                    continue

                # Pretty print a useful subset
                if event_name == "message" and data.get("type") == "status":
                    msg = data.get("msg")
                    url = data.get("url")
                    paper = data.get("paper_count")
                    kor = data.get("korean_authors")
                    print(f"[STATUS] {msg} | {url} | paper={paper} | korean_authors={kor}")
                else:
                    print(f"[EVENT:{event_name}] {data}")

                # Terminal events are carried inside payload: {"event":"done"| "error", ...}
                if data.get("event") in ("done", "error"):
                    terminal = data
                    break
            else:
                buffer_lines.append(raw_line)

    return terminal


async def fetch_result(client: httpx.AsyncClient, result_url: str) -> Dict:
    r = await client.get(result_url)
    # running이면 202로 올 수도 있음. 여기서는 그대로 출력.
    if r.status_code not in (200, 202, 500):
        r.raise_for_status()
    return r.json()


async def main():
    parser = argparse.ArgumentParser(description="Test FastAPI SSE crawl progress + final JSON result")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="FastAPI server base url")
    parser.add_argument("--payload", default=None, help="Path to JSON payload file for /api/search/start")
    args = parser.parse_args()

    # 기본 payload (너 스키마에 맞춰 조절)
    payload = {
        "option": 1,
        "uncertainty": 0.5,
        "startyear": 2024,
        "endyear": 2024,
        "countOption": False,
        "selectedConferences": ["CCS"],
    }

    if args.payload:
        with open(args.payload, "r", encoding="utf-8") as f:
            payload = json.load(f)

    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)  # read=None => long streaming allowed
    async with httpx.AsyncClient(timeout=timeout) as client:
        print("1) Starting job...")
        start_info = await start_job(client, args.base_url, payload)

        job_id = start_info["job_id"]
        events_url = _join_url(args.base_url, start_info["events_url"])
        result_url = _join_url(args.base_url, start_info["result_url"])

        print(f"   job_id     = {job_id}")
        print(f"   events_url = {events_url}")
        print(f"   result_url = {result_url}")

        print("\n2) Streaming SSE events...")
        terminal = await stream_events(client, events_url)

        print("\n3) Fetching final result JSON...")
        result = await fetch_result(client, result_url)

        print("\n=== TERMINAL EVENT ===")
        print(json.dumps(terminal, ensure_ascii=False, indent=2))

        print("\n=== RESULT JSON ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
