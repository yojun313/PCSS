# app/routes/auth.py
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import timedelta
import secrets

from app.core.templates import templates
from app.libs.email import send_email
from app.libs.logger import get_client_ip
from app.db import auth_col
from app.libs.auth import SESSION_COOKIE, now_utc, hash_code


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request=request, name="auth/login.html", context={"next": next}
    )


@router.post("/login/send", response_class=HTMLResponse)
async def login_send(request: Request, email: str = Form(...), next: str = Form("/")):
    ip = get_client_ip(request)
    now = now_utc()
    email_norm = (email or "").strip().lower()

    if not email_norm.endswith("@postech.ac.kr"):
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "next": next,
                "error": "POSTECH 이메일(@postech.ac.kr)만 사용 가능합니다.",
            },
        )

    # (이하 기존 로직 그대로)
    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = hash_code(email_norm, code)
    expires_at = now + timedelta(minutes=10)

    auth_col.update_one(
        {"kind": "otp", "email": email_norm},
        {
            "$set": {
                "kind": "otp",
                "email": email_norm,
                "code_hash": code_hash,
                "expires_at": expires_at,
                "tries": 0,
                "created_at": now,
                "ip": ip,
            }
        },
        upsert=True,
    )

    send_email(
        receiver=email_norm,
        title="[PCSS] 로그인 인증 코드",
        text=f"PCSS 로그인 인증 코드입니다: {code}\n",
    )

    return templates.TemplateResponse(
        request=request,
        name="auth/login_verify.html",
        context={
            "email": email_norm,
            "next": next,
            "message": "인증 코드를 메일로 보냈습니다.",
        },
    )


@router.post("/login/verify", response_class=HTMLResponse)
async def login_verify(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    next: str = Form("/"),
):
    ip = get_client_ip(request)
    now = now_utc()
    email_norm = (email or "").strip().lower()
    code = (code or "").strip()

    otp = auth_col.find_one(
        {"kind": "otp", "email": email_norm, "expires_at": {"$gt": now}},
        {"_id": 0},
    )
    if not otp:
        return templates.TemplateResponse(
            request=request,
            name="auth/login_verify.html",
            context={
                "email": email_norm,
                "next": next,
                "error": "코드가 만료되었거나 없습니다.",
            },
        )

    tries = int(otp.get("tries", 0))
    if tries >= 5:
        return templates.TemplateResponse(
            request=request,
            name="auth/login_verify.html",
            context={
                "email": email_norm,
                "next": next,
                "error": "시도 횟수를 초과했습니다. 다시 요청하세요.",
            },
        )

    expected = otp.get("code_hash")
    given = hash_code(email_norm, code)
    if not expected or not secrets.compare_digest(expected, given):
        auth_col.update_one(
            {"kind": "otp", "email": email_norm},
            {"$inc": {"tries": 1}},
        )
        return templates.TemplateResponse(
            request=request,
            name="auth/login_verify.html",
            context={
                "email": email_norm,
                "next": next,
                "error": "인증 코드가 올바르지 않습니다.",
            },
        )

    auth_col.delete_one({"kind": "otp", "email": email_norm})

    session_id = secrets.token_urlsafe(32)
    sess_expires = now + timedelta(days=14)

    auth_col.insert_one(
        {
            "kind": "session",
            "session_id": session_id,
            "email": email_norm,
            "expires_at": sess_expires,
            "created_at": now,
            "ip": ip,
        }
    )

    resp = RedirectResponse(url=next or "/", status_code=302)
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=14 * 24 * 3600,
    )
    return resp


@router.post("/logout")
async def logout(request: Request, next: str = "/"):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        auth_col.delete_many({"kind": "session", "session_id": sid})
    resp = RedirectResponse(url=next or "/", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
