# app/routes/user_routes.py
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.templates import templates
from app.db import users_col, auth_col, admin_col
from app.libs.auth import (
    SESSION_COOKIE,
    require_login,
    hash_password,
    verify_password,
    password_problem,
    now_utc,
)

router = APIRouter()


def _load(request: Request):
    """(user_doc, redirect_response) 형태로 돌려준다. 로그인 상태가 아니면 redirect가 채워진다."""
    email = require_login(request)
    if not email:
        return None, RedirectResponse("/auth/login?next=/profile", status_code=302)

    user = users_col.find_one({"email": email})
    if not user:
        # 세션은 남아 있는데 계정이 없는 경우 -> 세션을 정리하고 로그인으로 보낸다
        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            auth_col.delete_many({"kind": "session", "session_id": sid})
        resp = RedirectResponse("/auth/login?next=/profile", status_code=302)
        resp.delete_cookie(SESSION_COOKIE)
        return None, resp

    return user, None


def _render(request: Request, user: dict, status_code: int = 200, **extra):
    ctx = {
        "email": user["email"],
        "name": user.get("name", ""),
        "created_at": user.get("created_at"),
        "is_admin": admin_col.find_one({"email": user["email"]}, {"_id": 1})
        is not None,
    }
    ctx.update(extra)
    return templates.TemplateResponse(
        request=request,
        name="auth/profile.html",
        context=ctx,
        status_code=status_code,
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user, redirect = _load(request)
    if redirect:
        return redirect
    return _render(request, user)


@router.post("/profile/name", response_class=HTMLResponse)
async def change_name(request: Request, name: str = Form("")):
    user, redirect = _load(request)
    if redirect:
        return redirect

    new_name = (name or "").strip()
    if len(new_name) > 50:
        return _render(request, user, 400, error="이름은 50자 이하로 입력해 주세요.")

    users_col.update_one({"_id": user["_id"]}, {"$set": {"name": new_name}})
    user = users_col.find_one({"_id": user["_id"]})
    return _render(request, user, message="이름을 변경했습니다.")


@router.post("/profile/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
):
    user, redirect = _load(request)
    if redirect:
        return redirect

    if not verify_password(current_password, user.get("password_hash", "")):
        return _render(request, user, 400, error="현재 비밀번호가 올바르지 않습니다.")

    if new_password != new_password2:
        return _render(
            request, user, 400, error="새 비밀번호가 서로 일치하지 않습니다."
        )

    problem = password_problem(new_password)
    if problem:
        return _render(request, user, 400, error=problem)

    if verify_password(new_password, user.get("password_hash", "")):
        return _render(
            request, user, 400, error="현재 비밀번호와 다른 비밀번호를 사용해 주세요."
        )

    users_col.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(new_password),
                "password_changed_at": now_utc(),
            }
        },
    )

    # 비밀번호를 바꿨으면 지금 쓰는 기기를 뺀 나머지 세션은 모두 끊는다
    sid = request.cookies.get(SESSION_COOKIE)
    auth_col.delete_many(
        {"kind": "session", "email": user["email"], "session_id": {"$ne": sid}}
    )

    user = users_col.find_one({"_id": user["_id"]})
    return _render(
        request,
        user,
        message="비밀번호를 변경했습니다. 다른 기기에서의 로그인은 모두 해제되었습니다.",
    )
