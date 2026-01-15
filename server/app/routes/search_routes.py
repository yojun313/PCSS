from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from uuid import uuid4
import asyncio
import json
import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse
from app.libs.exceptions import NotFoundException, InternalServerErrorException
from app.schemas.search import SearchRequest
from app.services.search_service import PCSSEARCH
from app.core.job_store import create_job, get_job, finish_job, fail_job
from app.libs.exceptions import NotFoundException, InternalServerErrorException
from app.schemas.search import AuthorStatsRequest, AuthorStatsResponse
from app.services.search_service import compute_author_stats, fetch_html
from app.data import get_conferences_for_ui
from app.core.templates import templates

router = APIRouter()

@router.get("/conferences")
async def conferences():
    return JSONResponse(get_conferences_for_ui())

# 1) 작업 시작: job_id 즉시 반환
@router.post("/search/start")
async def start_search(req: SearchRequest):
    job_id = str(uuid4())
    options = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    job = create_job(job_id, options=options)

    pcs = PCSSEARCH(
        option=req.option,
        threshold=req.uncertainty,
        startyear=req.startyear,
        endyear=req.endyear,
        countOption=req.countOption,
        job_id=job_id,
        event_queue=job.queue,
    )

    async def runner():
        try:
            job.queue.put_nowait({"type": "event", "event": "started", "job_id": job_id})
            result = await pcs.run(req.selectedConferences)
            finish_job(job_id, result if isinstance(result, dict) else {"result": result})
            job.queue.put_nowait({"type": "event", "event": "done", "job_id": job_id})
        except Exception as e:
            fail_job(job_id, str(e))
            try:
                job.queue.put_nowait({"type": "event", "event": "error", "job_id": job_id, "error": str(e)})
            except Exception:
                pass

    asyncio.create_task(runner())

    return {
        "job_id": job_id,
        "events_url": f"/api/search/events/{job_id}",
        "result_url": f"/api/search/result/{job_id}",
        "page_url": f"/api/search/page/{job_id}",
    }

# 2) SSE 이벤트 스트림
@router.get("/search/events/{job_id}")
async def search_events(job_id: str):
    job = get_job(job_id)
    if not job:
        raise NotFoundException("job not found")

    async def event_generator():
        # 초기 연결 이벤트
        yield _sse("event", {"event": "connected", "job_id": job_id})

        while True:
            try:
                # 15초마다 heartbeat (프록시/브라우저 연결 유지에 도움)
                item = await asyncio.wait_for(job.queue.get(), timeout=15.0)
                payload = item if isinstance(item, dict) else {"type": "status", "msg": str(item)}

                # status/log는 "message" 이벤트로 통일
                if payload.get("type") == "status":
                    yield _sse("message", payload)
                else:
                    # started/done/error 등
                    yield _sse("event", payload)

                # done/error면 스트림 종료
                if payload.get("event") in ("done", "error"):
                    break

            except asyncio.TimeoutError:
                yield _sse("ping", {"event": "ping", "job_id": job_id})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

def _sse(event_name: str, data: dict) -> str:
    # SSE 포맷: event: <name>\ndata: <json>\n\n
    return f"event: {event_name}\n" + f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

# 3) 최종 JSON 결과 조회
@router.get("/search/result/{job_id}")
async def search_result(job_id: str):
    job = get_job(job_id)
    if not job:
        raise NotFoundException("job not found")

    if job.status == "running":
        return JSONResponse({"status": "running"}, status_code=202)

    if job.status == "error":
        raise InternalServerErrorException(job.error)
        

    return {"status": "done", "result": job.result}


@router.get("/search/page/{job_id}", response_class=HTMLResponse)
async def search_page(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise NotFoundException("job not found")

    if job.status == "error":
        raise InternalServerErrorException(job.error or "unknown error")

    options = job.options or {}

    option_map = {
        1: "1저자가 한국인",
        2: "1저자 또는 2저자가 한국인",
        3: "마지막 저자가 한국인",
        4: "1저자 또는 마지막 저자가 한국인",
        5: "저자 중 한 명 이상이 한국인",
    }
    option_text = option_map.get(int(options.get("option", 0)), "옵션 미선택")

    if job.status == "running":
        return templates.TemplateResponse(
            "loading_result.html",
            {
                "request": request,
                "job_id": job_id,
                "events_url": f"/api/search/events/{job_id}",
                "page_url": f"/api/search/page/{job_id}",
                "options": options,
                "option_text": option_text,
            },
        )

    # done이면 결과 렌더링
    pythonResult = job.result or {}
    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "options": options,
            "option_text": option_text,
            "pythonResult": pythonResult,
            "FASTAPI_BASE": "http://pcss.r-e.kr:8000",
        },
    )


@router.post("/author-stats", response_model=AuthorStatsResponse)
async def author_stats(payload: AuthorStatsRequest):
    try:
        html = await fetch_html(str(payload.url), payload.timeout_sec)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch url: {e}") from e

    result = compute_author_stats(
        html=html,
        target_author=payload.target_author,
        max_retry=payload.max_retry,
    )

    # include_papers가 아니면 papers 필드 제거
    if not payload.include_papers:
        result.pop("papers", None)

    return result
