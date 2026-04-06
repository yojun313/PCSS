from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from uuid import uuid4
import asyncio
import json
from app.schemas.search import SearchRequest
from app.services.search_service import PCSSEARCHMongo
from app.core.job_store import (
    create_job,
    get_job,
    finish_job,
    fail_job,
    cancel_job,
)
from app.data import get_conferences_for_ui
from app.core.templates import templates
from app.db import log_col
from datetime import datetime, timezone
from app.libs.logger import get_client_ip
from app.libs.auth import get_session_email


router = APIRouter()


@router.get("/conferences")
async def conferences():
    return JSONResponse(get_conferences_for_ui())


@router.post("/start")
async def start_search(req: SearchRequest, request: Request):
    ip = get_client_ip(request)
    job_id = str(uuid4())
    options = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    
    try:
        if ip not in ["127.0.0.1", "::1"]:
            log_col.insert_one({
                "ts": datetime.now(timezone.utc),
                "type": "search_start",
                "ip": ip,
                "email": get_session_email(request),    
                "job_id": job_id,
                "options": options,
                "user_agent": request.headers.get("user-agent", ""),
                "referer": request.headers.get("referer", ""),
            })
    except Exception:
        print("Failed to log search start event")
    
    job = create_job(job_id, options=options)

    pcs = PCSSEARCHMongo(
        options=req.options,
        threshold=req.probability,
        startyear=req.startyear,
        endyear=req.endyear,
        countOption=req.countOption,
        job_id=job_id,
        event_queue=job.queue,
        cancel_check=lambda: bool(get_job(job_id) and get_job(job_id).cancel_requested),
    )

    async def runner():
        try:
            try:
                job.queue.put_nowait({"type": "event", "event": "started", "job_id": job_id})
            except Exception:
                pass

            result = await pcs.run(req.selectedConferences)

            finish_job(job_id, result if isinstance(result, dict) else {"result": result})

            try:
                job.queue.put_nowait({"type": "event", "event": "done", "job_id": job_id})
            except Exception:
                pass

        except asyncio.CancelledError:
            try:
                job.queue.put_nowait({"type": "event", "event": "cancelled", "job_id": job_id})
            except Exception:
                pass

        except Exception as e:
            fail_job(job_id, str(e))
            try:
                job.queue.put_nowait(
                    {"type": "event", "event": "error", "job_id": job_id, "error": str(e)}
                )
            except Exception:
                pass

    task = asyncio.create_task(runner())
    job.task = task

    return {
        "job_id": job_id,
        "events_url": f"/api/search/events/{job_id}",
        "result_url": f"/api/search/result/{job_id}",
        "page_url": f"/api/search/page/{job_id}",
        "cancel_url": f"/api/search/cancel/{job_id}",
    }


@router.get("/events/{job_id}")
async def search_events(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    async def event_generator():
        # 초기 연결 이벤트
        yield _sse("event", {"event": "connected", "job_id": job_id})

        while True:
            try:
                # 15초마다 heartbeat
                item = await asyncio.wait_for(job.queue.get(), timeout=15.0)
                payload = item if isinstance(item, dict) else {"type": "status", "msg": str(item)}

                # status는 message 이벤트로
                if payload.get("type") == "status":
                    yield _sse("message", payload)
                else:
                    yield _sse("event", payload)

                # 종료 이벤트면 스트림 종료
                if payload.get("event") in ("done", "error", "cancelled"):
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
    return (
        f"event: {event_name}\n"
        + f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


@router.get("/result/{job_id}")
async def search_result(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status == "cancelled":
        return JSONResponse({"status": "cancelled"}, status_code=200)

    if job.status == "running":
        return JSONResponse({"status": "running"}, status_code=202)

    if job.status == "error":
        raise HTTPException(500, job.error)

    return {"status": "done", "result": job.result}


@router.get("/page/{job_id}", response_class=HTMLResponse)
async def search_page(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    if job.status == "error":
        raise HTTPException(500, job.error or "unknown error")

    options = job.options or {}

    option_map = {
        1: "1저자",
        2: "2저자",
        3: "마지막 저자",
        4: "기타 공저자",
    }
    
    raw_option = options.get("options", [])
    if isinstance(raw_option, int):
        raw_option = [raw_option]

    # 3. 리스트 순회하며 텍스트로 변환
    selected_texts = []
    if raw_option and isinstance(raw_option, list):
        for opt in raw_option:
            txt = option_map.get(int(opt))
            if txt:
                selected_texts.append(txt)
    
    # 4. 콤마로 연결하여 하나의 문자열로 생성 (예: "1저자, 마지막 저자")
    if selected_texts:
        option_text = ", ".join(selected_texts)
    else:
        option_text = "옵션 미선택"

    if job.status == "running":
        return templates.TemplateResponse(
            "search/loading_result.html",
            {
                "request": request,
                "job_id": job_id,
                "events_url": f"/api/search/events/{job_id}",
                "page_url": f"/api/search/page/{job_id}",
                "cancel_url": f"/api/search/cancel/{job_id}",
                "options": options,
                "option_text": option_text,
            },
        )

    pythonResult = job.result or {}
    return templates.TemplateResponse(
        "search/results.html",
        {
            "request": request,
            "options": options,
            "option_text": option_text,
            "pythonResult": pythonResult,
            "FASTAPI_BASE": "http://pcss.r-e.kr:8000",
        },
    )


@router.post("/cancel/{job_id}")
async def search_cancel(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    ok = cancel_job(job_id)
    if ok:
        # SSE 즉시 반영 이벤트
        try:
            job.queue.put_nowait({"type": "event", "event": "cancelled", "job_id": job_id})
        except Exception:
            pass
        return {"status": "cancelled", "job_id": job_id}

    return {"status": "failed", "job_id": job_id}
