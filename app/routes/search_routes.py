from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, Response
from typing import Optional
from urllib.parse import urlencode
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
from app.services.insights_service import result_author_meta
from app.services.export_service import build_export_rows, to_csv, to_xlsx


router = APIRouter()


def search_query_string(options: dict) -> str:
    """검색 조건 -> 메인 페이지가 읽는 공유용 쿼리 (?c=AAAI,IJCAI&s=2020&e=2025&p=0.7&o=1,3)"""
    raw_option = options.get("options", [])
    if isinstance(raw_option, int):
        raw_option = [raw_option]
    return urlencode(
        {
            "c": ",".join(options.get("selectedConferences") or []),
            "s": options.get("startyear", ""),
            "e": options.get("endyear", ""),
            "p": options.get("probability", ""),
            "o": ",".join(str(o) for o in raw_option or []),
        }
    )


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
            log_col.insert_one(
                {
                    "ts": datetime.now(timezone.utc),
                    "type": "search_start",
                    "ip": ip,
                    "email": get_session_email(request),
                    "job_id": job_id,
                    "options": options,
                    "user_agent": request.headers.get("user-agent", ""),
                    "referer": request.headers.get("referer", ""),
                }
            )
    except Exception:
        print("Failed to log search start event")

    job = create_job(job_id, options=options)

    pcs = PCSSEARCHMongo(
        options=req.options,
        threshold=req.probability,
        startyear=req.startyear,
        endyear=req.endyear,
        job_id=job_id,
        event_queue=job.queue,
        cancel_check=lambda: bool(get_job(job_id) and get_job(job_id).cancel_requested),
    )

    async def runner():
        try:
            try:
                job.queue.put_nowait(
                    {"type": "event", "event": "started", "job_id": job_id}
                )
            except Exception:
                pass

            result = await pcs.run(req.selectedConferences)

            finish_job(
                job_id, result if isinstance(result, dict) else {"result": result}
            )

            try:
                job.queue.put_nowait(
                    {"type": "event", "event": "done", "job_id": job_id}
                )
            except Exception:
                pass

        except asyncio.CancelledError:
            try:
                job.queue.put_nowait(
                    {"type": "event", "event": "cancelled", "job_id": job_id}
                )
            except Exception:
                pass

        except Exception as e:
            fail_job(job_id, str(e))
            try:
                job.queue.put_nowait(
                    {
                        "type": "event",
                        "event": "error",
                        "job_id": job_id,
                        "error": str(e),
                    }
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
                payload = (
                    item
                    if isinstance(item, dict)
                    else {"type": "status", "msg": str(item)}
                )

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
        f"event: {event_name}\n" + f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
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
            request=request,
            name="search/loading_result.html",
            context={
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
        request=request,
        name="search/results.html",
        context={
            "job_id": job_id,
            "options": options,
            "option_text": option_text,
            "pythonResult": pythonResult,
            "author_meta": await result_author_meta(pythonResult),
            "requery_url": "/?" + search_query_string(options),
            "FASTAPI_BASE": "http://pcss.postech.ac.kr:8000",
        },
    )


@router.get("/export/{job_id}")
async def search_export(
    job_id: str,
    fmt: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    q: str = "",
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0),
    conference: str = "",
):
    job = get_job(job_id)
    if not job or job.status != "done":
        raise HTTPException(404, "검색 결과가 없거나 아직 끝나지 않았습니다.")

    options = job.options or {}
    result = job.result or {}
    rows = build_export_rows(
        result,
        await result_author_meta(result),
        q=q,
        min_score=min_score,
        conference=conference,
    )

    stamp = datetime.now().strftime("%Y%m%d")
    if fmt == "csv":
        body, media = to_csv(rows), "text/csv; charset=utf-8"
    else:
        filters = [
            ["이름 검색", q or "-"],
            ["최소 확률", min_score if min_score is not None else "-"],
            ["학회 필터", conference or "전체"],
        ]
        options_rows = [
            ["연도 범위", f"{options.get('startyear')} ~ {options.get('endyear')}"],
            ["한국인 판단 기준값", options.get("probability")],
            ["선택 학회", ", ".join(options.get("selectedConferences") or [])],
            *filters,
            [
                "같은 조건으로 다시 검색",
                "https://pcss.postech.ac.kr/?" + search_query_string(options),
            ],
        ]
        body = to_xlsx(rows, options_rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    filename = f"pcss_{stamp}.{fmt}"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
            job.queue.put_nowait(
                {"type": "event", "event": "cancelled", "job_id": job_id}
            )
        except Exception:
            pass
        return {"status": "cancelled", "job_id": job_id}

    return {"status": "failed", "job_id": job_id}
