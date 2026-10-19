from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from urllib.parse import quote
from fastapi import Form
from app.core.templates import templates
from app.services.insights_service import (
    ROLE_LABELS,
    compute_author_page,
    compute_rankings,
    get_profiles,
)
from app.db import name_col, req_korean_col, authors_col
from app.data import name_dict, author_list
from datetime import datetime, timezone
from app.libs.auth import _require_admin_or_redirect
import asyncio
import re
import math

router = APIRouter()


def author_page_url(pid: str, probability: float | None = None) -> str:
    # pid 에는 '/' 가 들어간다 (예: 81/10063) → 경로 그대로 두고 나머지만 인코딩
    url = f"/author/p/{quote(pid, safe='/')}"
    return f"{url}?prob={probability}" if probability is not None else url


@router.post("/stats/page")
async def author_stats_page_legacy(
    target_pid: str = Form(...),
    probability: float = Form(0.8),
):
    # 예전 POST 폼 · 북마크 호환: 공유 가능한 GET 주소로 보낸다
    return RedirectResponse(author_page_url(target_pid, probability), status_code=303)


@router.get("/p/{pid:path}", response_class=HTMLResponse)
async def author_page(
    request: Request,
    pid: str,
    prob: float = Query(0.8, ge=0.0, le=1.0),
):
    data = await compute_author_page(pid)
    if not data["papers"] and not data["profile"]:
        raise HTTPException(404, "Author not found")

    score = name_dict.get(data["name"], 0)
    is_korean = score >= prob

    return templates.TemplateResponse(
        request=request,
        name="search/author_stats.html",
        context={
            **data,
            "url": f"https://dblp.org/pid/{pid}.html",
            "probability": prob,
            "score": score,
            "is_korean": is_korean,
            "role_labels": ROLE_LABELS,
        },
    )


@router.get("/search", response_class=HTMLResponse)
async def author_search_input_page(request: Request):
    """
    저자 이름을 검색하여 통계 페이지로 이동하기 위한 입력 폼 페이지
    """
    # 검색창 아래에 보여 줄 '최근 5년 1저자 논문이 많은 연구자' - 랭킹 집계 캐시를 그대로 쓴다.
    # 캐시가 비어 있으면 집계가 길어질 수 있어 잠깐만 기다리고, 넘치면 그냥 비워 둔다.
    top = []
    try:
        end = datetime.now().year
        data = await asyncio.wait_for(
            compute_rankings("all", end - 4, end, "first", 0.7, False, limit=12), 3
        )
        top = data.get("rows", [])
    except Exception:
        top = []
    return templates.TemplateResponse(
        request=request,
        name="search/author_search_input.html",
        context={"request": request, "top": top, "top_start": datetime.now().year - 4},
    )


@router.get("/autocomplete")
async def author_autocomplete(query: str = Query(..., min_length=1)):
    try:
        # 1. 검색어 전처리: 공백 기준 분리
        keywords = query.strip().split()

        if not keywords:
            return JSONResponse(content=[])

        # 2. 검색어 정규화 (공백 제거 + 소문자화, name_norm/pid_norm과 동일한 규칙)
        norm_keywords = [w.lower().replace(" ", "") for w in keywords]

        results = []
        count = 0

        global_authors = author_list if "author_list" in globals() else []

        for author in global_authors:
            name = author.get("name", "")
            pid = author.get("pid", "")

            if not pid:
                continue

            name_norm = author.get("name_norm", "")
            pid_norm = author.get("pid_norm", "")

            is_match = True
            for kw in norm_keywords:
                # 부분 문자열 포함 여부만 확인 (기존 정규식과 동일한 매칭 결과)
                if kw not in name_norm and kw not in pid_norm:
                    is_match = False
                    break  # 하나라도 매칭 안 되면 즉시 중단 (Pruning)

            if is_match:
                results.append(
                    {
                        "name": name,
                        "pid": pid,
                    }
                )
                count += 1

            # 최대 10개만 찾고 종료
            if count >= 10:
                break

        # 동명이인을 구분하도록 소속을 붙인다 (템플릿은 affiliation 이 있으면 PID 대신 보여 준다)
        profiles = await get_profiles(r["pid"] for r in results)
        for r in results:
            r["affiliation"] = profiles.get(r["pid"], {}).get("affiliation", "")

        return JSONResponse(content=results)

    except Exception as e:
        print(f"[Autocomplete Error] {e}")
        return JSONResponse(content=[], status_code=500)


@router.get("/manage/names", response_class=HTMLResponse)
async def manage_all_names(
    request: Request,
    page: int = Query(1, gt=0),
    q: str = Query("", description="검색할 이름"),
):
    # 1. 관리자 권한 체크
    next_path = str(request.url)
    email, status_or_resp = _require_admin_or_redirect(request, next_path)

    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp

    if status_or_resp is None:
        return templates.TemplateResponse(
            request=request,
            name="admin/access_denied.html",
            context={"error": "관리자 권한이 필요합니다."},
            status_code=403,
        )

    # 2. 검색 및 페이징 설정
    limit = 100
    skip = (page - 1) * limit

    mongo_query = {}
    if q.strip():
        # 대소문자 구분 없는 부분 일치 검색 (Regex)
        mongo_query["name"] = {"$regex": re.escape(q.strip()), "$options": "i"}

    # 3. 데이터 조회 (PyMongo 동기 호출)
    # 전체 개수 계산 (페이지 계산용)
    total_count = name_col.count_documents(mongo_query)
    total_pages = math.ceil(total_count / limit)

    # 데이터 가져오기 (이름 순 정렬)
    cursor = name_col.find(mongo_query).sort("name", 1).skip(skip).limit(limit)
    authors_data = list(cursor)

    return templates.TemplateResponse(
        request=request,
        name="admin/manage_names.html",
        context={
            "authors": authors_data,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "q": q,
            "email": email,
        },
    )


@router.get("/request/korean")
async def get_korean_requests():
    requests = list(req_korean_col.find({}))
    response = []
    for req in requests:
        response.append(
            {
                "name": req["name"],
                "score": req["score"],
                "type": req["type"],
                "updated_at": req["updated_at"],
            }
        )
    return {"requests": response}


@router.post("/korean/{param}")
async def add_korean_stat(param: str):
    if param not in name_dict:
        raise HTTPException(404, "Name not found")

    name_dict[param] = 1

    result = name_col.update_one(
        {"name": param},
        {"$set": {"score": 1, "updated_at": datetime.now(timezone.utc)}},
    )

    if result.matched_count == 0:
        # dict에는 있는데 DB에는 없는 상태 -> 데이터 불일치
        raise HTTPException(404, "Name not found in DB")

    return {"status": "Updated", "param": param}


@router.delete("/korean/{param}")
async def delete_korean_stat(param: str):
    if param not in name_dict:
        raise HTTPException(404, "Name not found")

    name_dict[param] = 0

    result = name_col.update_one(
        {"name": param},
        {"$set": {"score": 0, "updated_at": datetime.now(timezone.utc)}},
    )

    if result.matched_count == 0:
        raise HTTPException(404, "Name not found in DB")

    return {"status": "Deleted", "param": param}


@router.post("/add/korean/{param}")
async def request_add_korean_stat(param: str):
    if param not in name_dict:
        raise HTTPException(404, "Name not found")

    req_korean_col.update_one(
        {"name": param},
        {
            "$set": {
                "name": param,
                "score": name_dict[param],
                "type": 1,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return {"status": "Requested", "param": param}


@router.post("/cancel/korean/{param}")
async def cancel_korean_request(param: str):
    if param not in name_dict:
        raise HTTPException(404, "Name not found")

    result = req_korean_col.delete_one({"name": param})

    if result.deleted_count == 0:
        # 취소할 요청이 없을 때
        raise HTTPException(404, "No pending request to cancel")

    return {"status": "Canceled", "param": param}


@router.post("/delete/korean/{param}")
async def request_delete_korean_stat(param: str):
    if param not in name_dict:
        raise HTTPException(404, "Name not found")

    req_korean_col.update_one(
        {"name": param},
        {
            "$set": {
                "name": param,
                "score": name_dict[param],
                "type": 0,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return {"status": "Requested", "param": param}


@router.get("/manage", response_class=HTMLResponse)
async def admin_requests_page(request: Request):
    next_path = request.url.path  # "/author/requests"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)

    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp

    if status_or_resp is None:
        # 로그인은 했는데 admin 아님
        return templates.TemplateResponse(
            request=request,
            name="admin/access_denied.html",
            context={"error": "권한이 없습니다. 관리자 계정으로 로그인하세요."},
            status_code=403,
        )

    reqs = list(req_korean_col.find({}).sort("updated_at", -1))

    items = []
    for r in reqs:
        items.append(
            {
                "name": r.get("name", ""),
                "score": r.get("score", 0),
                "type": r.get("type", None),
                "updated_at": r.get("updated_at", None).isoformat()
                if r.get("updated_at", None)
                else "",
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_name_requests.html",
        context={"requests": items, "error": None, "email": email},
    )


def apply_korean_score(param: str, score: int):
    """
    add_korean_stat / delete_korean_stat와 동일한 핵심 로직
    (권장: DB 성공 후 dict 반영)
    """
    if param not in name_dict:
        raise HTTPException(404, "Name not found")

    result = name_col.update_one(
        {"name": param},
        {"$set": {"score": score, "updated_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Name not found in DB")

    name_dict[param] = score


@router.post("/requests/approve")
async def approve_korean_request(request: Request, name: str = Form(...)):
    next_path = "/author/manage"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)

    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        return RedirectResponse(url=next_path, status_code=303)

    req_doc = req_korean_col.find_one({"name": name})
    if not req_doc:
        raise HTTPException(404, "No pending request")

    req_type = req_doc.get("type", None)
    if req_type not in (0, 1):
        raise HTTPException(404, "Invalid request type")

    new_score = 1 if req_type == 1 else 0
    apply_korean_score(name, new_score)

    req_korean_col.delete_one({"name": name})
    return RedirectResponse(url=next_path, status_code=303)


@router.post("/requests/deny")
async def deny_korean_request(request: Request, name: str = Form(...)):
    next_path = "/author/manage"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)

    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        return RedirectResponse(url=next_path, status_code=303)

    result = req_korean_col.delete_one({"name": name})
    if result.deleted_count == 0:
        raise HTTPException(404, "No pending request to deny")

    return RedirectResponse(url=next_path, status_code=303)
