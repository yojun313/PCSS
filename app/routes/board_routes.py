# app/routes/board.py

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from bson import ObjectId
from datetime import datetime, timezone

from app.core.templates import templates
# admin_col이 꼭 포함되어야 합니다.
from app.db import board_notice_col, board_bug_col, admin_col
from app.libs.auth import _require_admin_or_redirect, get_session_email

router = APIRouter()

# 게시판 메인
@router.get("/", response_class=HTMLResponse)
async def board_list(request: Request):
    # 최신순 정렬
    notices = list(board_notice_col.find({}).sort("created_at", -1))
    bugs = list(board_bug_col.find({}).sort("created_at", -1))
    
    email = get_session_email(request)
    
    is_admin = False
    if email:
        if admin_col.find_one({"email": email}):
            is_admin = True

    return templates.TemplateResponse(
        request=request,
        name="board/list.html",
        context={
            "notices": notices,
            "bugs": bugs,
            "email": email,
            "is_admin": is_admin
        }
    )

# 버그 상태 변경 API (관리자 전용)
@router.post("/bug/status/{oid}")
async def bug_status_update(request: Request, oid: str, status: str = Form(...)):
    # 관리자 권한 체크
    email, resp = _require_admin_or_redirect(request, "/board")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    # 상태 업데이트
    board_bug_col.update_one(
        {"_id": ObjectId(oid)},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}}
    )
    
    # 처리가 끝나면 게시판 메인으로 리다이렉트
    return RedirectResponse(url="/board", status_code=302)



# [관리자] 공지사항 작성 페이지
@router.get("/notice/write", response_class=HTMLResponse)
async def notice_write_page(request: Request):
    email, resp = _require_admin_or_redirect(request, "/board/notice/write")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    return templates.TemplateResponse(
        request=request,
        name="board/admin_page.html",
        context={
            "email": email,
            "mode": "create"
        }
    )


# [관리자] 공지사항 저장
@router.post("/notice/write")
async def notice_create(request: Request, title: str = Form(...), content: str = Form(...)):
    email, resp = _require_admin_or_redirect(request, "/board/notice/write")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    board_notice_col.insert_one({
        "title": title,
        "content": content,
        "author": email,
        "created_at": datetime.now(timezone.utc),
        "views": 0
    })
    return RedirectResponse(url="/board", status_code=302)

# [관리자] 공지사항 수정 페이지
@router.get("/notice/edit/{oid}", response_class=HTMLResponse)
async def notice_edit_page(request: Request, oid: str):
    email, resp = _require_admin_or_redirect(request, f"/board/notice/edit/{oid}")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    notice = board_notice_col.find_one({"_id": ObjectId(oid)})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    return templates.TemplateResponse(
        request=request,
        name="board/admin_page.html",
        context={
            "email": email,
            "notice": notice,
            "mode": "edit"
        }
    )

# [관리자] 공지사항 수정 처리
@router.post("/notice/edit/{oid}")
async def notice_update(request: Request, oid: str, title: str = Form(...), content: str = Form(...)):
    email, resp = _require_admin_or_redirect(request, "/board")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    board_notice_col.update_one(
        {"_id": ObjectId(oid)},
        {"$set": {
            "title": title,
            "content": content,
            "updated_at": datetime.now(timezone.utc)
        }}
    )
    # 수정 후 상세 페이지로 이동
    return RedirectResponse(url=f"/board/notice/{oid}", status_code=302)

# [관리자] 공지사항 삭제 처리
@router.post("/notice/delete/{oid}")
async def notice_delete(request: Request, oid: str):
    email, resp = _require_admin_or_redirect(request, "/board")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    board_notice_col.delete_one({"_id": ObjectId(oid)})
    return RedirectResponse(url="/board", status_code=302)


# 공지사항 상세 보기 (일반 사용자 + 관리자)
@router.get("/notice/{oid}", response_class=HTMLResponse)
async def notice_detail(request: Request, oid: str):
    notice = board_notice_col.find_one({"_id": ObjectId(oid)})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    
    board_notice_col.update_one({"_id": ObjectId(oid)}, {"$inc": {"views": 1}})
    
    # 상세 페이지에서도 수정/삭제 버튼을 보여주기 위해 관리자 여부 확인
    email = get_session_email(request)
    is_admin = False
    if email:
        if admin_col.find_one({"email": email}):
            is_admin = True
    
    return templates.TemplateResponse(
        request=request,
        name="board/notice_detail.html",
        context={
            "notice": notice,
            "is_admin": is_admin
        }
    )


# 버그 제보 작성 페이지
@router.get("/bug/write", response_class=HTMLResponse)
async def bug_write_page(request: Request):
    email = get_session_email(request)
    if not email:
        return RedirectResponse(url="/auth/login?next=/board/bug/write", status_code=302)
    
    return templates.TemplateResponse(
        request=request,
        name="board/bug_write.html",
        context={
            "email": email,
            "mode": "create"
        }
    )

# 버그 제보 저장
@router.post("/bug/write")
async def bug_create(request: Request, title: str = Form(...), content: str = Form(...)):
    email = get_session_email(request)
    if not email:
        return RedirectResponse(url="/auth/login", status_code=302)

    board_bug_col.insert_one({
        "title": title,
        "content": content,
        "author": email,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "status": "접수됨"
    })
    return RedirectResponse(url="/board", status_code=302)

# 버그 제보 상세 보기
@router.get("/bug/{oid}", response_class=HTMLResponse)
async def bug_detail(request: Request, oid: str):
    bug = board_bug_col.find_one({"_id": ObjectId(oid)})
    if not bug:
        raise HTTPException(status_code=404, detail="Bug report not found")
    
    current_email = get_session_email(request)
    # 본인이 작성한 글인지 확인
    is_owner = (current_email == bug.get("author"))

    return templates.TemplateResponse(
        request=request,
        name="board/bug_detail.html",
        context={
            "bug": bug,
            "is_owner": is_owner,
            "current_email": current_email
        }
    )

# 버그 제보 수정 페이지
@router.get("/bug/edit/{oid}", response_class=HTMLResponse)
async def bug_edit_page(request: Request, oid: str):
    bug = board_bug_col.find_one({"_id": ObjectId(oid)})
    email = get_session_email(request)
    
    # 본인 확인
    if not bug or not email or bug.get("author") != email:
        return RedirectResponse(url=f"/board/bug/{oid}", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="board/bug_write.html",
        context={
            "email": email,
            "mode": "edit",
            "bug": bug
        }
    )

# 버그 제보 수정 처리
@router.post("/bug/edit/{oid}")
async def bug_update(request: Request, oid: str, title: str = Form(...), content: str = Form(...)):
    bug = board_bug_col.find_one({"_id": ObjectId(oid)})
    email = get_session_email(request)

    if not bug or not email or bug.get("author") != email:
        return RedirectResponse(url="/board", status_code=302)

    board_bug_col.update_one(
        {"_id": ObjectId(oid)},
        {"$set": {
            "title": title, 
            "content": content, 
            "updated_at": datetime.now(timezone.utc)
        }}
    )
    return RedirectResponse(url=f"/board/bug/{oid}", status_code=302)

# 버그 제보 삭제 처리
@router.post("/bug/delete/{oid}")
async def bug_delete(request: Request, oid: str):
    bug = board_bug_col.find_one({"_id": ObjectId(oid)})
    email = get_session_email(request)

    if bug and email and bug.get("author") == email:
        board_bug_col.delete_one({"_id": ObjectId(oid)})
    
    return RedirectResponse(url="/board", status_code=302)