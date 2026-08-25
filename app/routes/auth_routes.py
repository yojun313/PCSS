# app/routes/auth.py
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import timedelta

from app.core.templates import templates
from app.libs.email import send_email
from app.libs.logger import get_client_ip
from app.db import auth_col, users_col, pending_signups_col, password_resets_col
from app.libs.auth import (
    SESSION_COOKIE,
    ALLOWED_EMAIL_DOMAIN,
    OTP_TTL_MINUTES,
    OTP_MAX_TRIES,
    now_utc,
    hash_code,
    verify_code,
    generate_code,
    normalize_email,
    is_allowed_email,
    hash_password,
    verify_password,
    password_problem,
    create_session,
    _safe_next,
)


router = APIRouter()


def _set_session(resp, email: str, ip: str):
    session_id, max_age = create_session(email, ip)
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=max_age,
    )
    return resp


# ---------------------------
# 로그인
# ---------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request=request, name="auth/login.html", context={"next": _safe_next(next)}
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    next = _safe_next(next)
    email_norm = normalize_email(email)

    user = users_col.find_one({"email": email_norm})
    if not user or not verify_password(password, user.get("password_hash", "")):
        # 계정 존재 여부를 알려주지 않도록 동일한 메시지를 쓴다
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "next": next,
                "email": email_norm,
                "error": "이메일 또는 비밀번호가 올바르지 않습니다.",
            },
            status_code=401,
        )

    now = now_utc()
    users_col.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now}})

    resp = RedirectResponse(url=next or "/", status_code=302)
    return _set_session(resp, email_norm, get_client_ip(request))


# ---------------------------
# 회원가입 1단계: 정보 입력 -> 인증 코드 발송
# ---------------------------
@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request=request,
        name="auth/signup.html",
        context={"next": _safe_next(next), "domain": ALLOWED_EMAIL_DOMAIN},
    )


@router.post("/signup", response_class=HTMLResponse)
async def signup_send_code(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    name: str = Form(""),
    next: str = Form("/"),
):
    next = _safe_next(next)
    email_norm = normalize_email(email)
    name = (name or "").strip()

    def fail(msg: str, status_code: int = 400):
        return templates.TemplateResponse(
            request=request,
            name="auth/signup.html",
            context={
                "next": next,
                "domain": ALLOWED_EMAIL_DOMAIN,
                "email": email_norm,
                "name": name,
                "error": msg,
            },
            status_code=status_code,
        )

    if not is_allowed_email(email_norm):
        return fail(f"@{ALLOWED_EMAIL_DOMAIN} 이메일만 가입할 수 있습니다.")

    if password != password2:
        return fail("비밀번호가 서로 일치하지 않습니다.")

    problem = password_problem(password)
    if problem:
        return fail(problem)

    if users_col.find_one({"email": email_norm}, {"_id": 1}):
        return fail("이미 가입된 이메일입니다. 로그인해 주세요.")

    now = now_utc()
    code = generate_code()

    # 비밀번호는 이 시점에 해싱해서 보관하고, 인증이 끝나면 그대로 users로 옮긴다
    pending_signups_col.update_one(
        {"email": email_norm},
        {
            "$set": {
                "email": email_norm,
                "name": name,
                "password_hash": hash_password(password),
                "code_hash": hash_code(email_norm, code),
                "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
                "tries": 0,
                "created_at": now,
                "ip": get_client_ip(request),
            }
        },
        upsert=True,
    )

    send_email(
        receiver=email_norm,
        title="[PCSS] 회원가입 인증 코드",
        text=(
            f"PCSS 회원가입 인증 코드입니다: {code}\n\n"
            f"이 코드는 {OTP_TTL_MINUTES}분 후 만료됩니다.\n"
            "본인이 요청하지 않았다면 이 메일을 무시하셔도 됩니다.\n"
        ),
    )

    return templates.TemplateResponse(
        request=request,
        name="auth/signup_verify.html",
        context={
            "email": email_norm,
            "next": next,
            "message": "인증 코드를 메일로 보냈습니다.",
        },
    )


# ---------------------------
# 회원가입 2단계: 인증 코드 확인 -> 계정 생성
# ---------------------------
@router.post("/signup/verify", response_class=HTMLResponse)
async def signup_verify(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    next: str = Form("/"),
):
    next = _safe_next(next)
    email_norm = normalize_email(email)
    code = (code or "").strip()
    now = now_utc()

    def fail(msg: str, status_code: int = 400):
        return templates.TemplateResponse(
            request=request,
            name="auth/signup_verify.html",
            context={"email": email_norm, "next": next, "error": msg},
            status_code=status_code,
        )

    pending = pending_signups_col.find_one(
        {"email": email_norm, "expires_at": {"$gt": now}}
    )
    if not pending:
        return fail("코드가 만료되었거나 없습니다. 다시 요청해 주세요.")

    if int(pending.get("tries", 0)) >= OTP_MAX_TRIES:
        pending_signups_col.delete_one({"email": email_norm})
        return fail("시도 횟수를 초과했습니다. 처음부터 다시 요청해 주세요.", 429)

    if not verify_code(email_norm, code, pending.get("code_hash", "")):
        pending_signups_col.update_one({"email": email_norm}, {"$inc": {"tries": 1}})
        return fail("인증 코드가 올바르지 않습니다.")

    pending_signups_col.delete_one({"email": email_norm})

    if users_col.find_one({"email": email_norm}, {"_id": 1}):
        return fail("이미 가입된 이메일입니다. 로그인해 주세요.")

    users_col.insert_one(
        {
            "email": email_norm,
            "password_hash": pending["password_hash"],
            "name": pending.get("name", ""),
            "created_at": now,
            "last_login_at": now,
        }
    )

    # 가입 즉시 로그인 상태로 만들어 준다
    resp = RedirectResponse(url=next or "/", status_code=302)
    return _set_session(resp, email_norm, get_client_ip(request))


# ---------------------------
# 비밀번호 찾기 1단계: 이메일 입력 -> 재설정 코드 발송
# ---------------------------
@router.get("/forgot", response_class=HTMLResponse)
async def forgot_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot.html",
        context={"domain": ALLOWED_EMAIL_DOMAIN},
    )


@router.post("/forgot", response_class=HTMLResponse)
async def forgot_send_code(request: Request, email: str = Form(...)):
    email_norm = normalize_email(email)

    if not is_allowed_email(email_norm):
        return templates.TemplateResponse(
            request=request,
            name="auth/forgot.html",
            context={
                "domain": ALLOWED_EMAIL_DOMAIN,
                "email": email_norm,
                "error": f"@{ALLOWED_EMAIL_DOMAIN} 이메일만 사용할 수 있습니다.",
            },
            status_code=400,
        )

    user = users_col.find_one({"email": email_norm}, {"_id": 1})
    if user:
        now = now_utc()
        code = generate_code()
        password_resets_col.update_one(
            {"email": email_norm},
            {
                "$set": {
                    "email": email_norm,
                    "code_hash": hash_code(email_norm, code),
                    "expires_at": now + timedelta(minutes=OTP_TTL_MINUTES),
                    "tries": 0,
                    "created_at": now,
                    "ip": get_client_ip(request),
                }
            },
            upsert=True,
        )
        send_email(
            receiver=email_norm,
            title="[PCSS] 비밀번호 재설정 코드",
            text=(
                f"PCSS 비밀번호 재설정 코드입니다: {code}\n\n"
                f"이 코드는 {OTP_TTL_MINUTES}분 후 만료됩니다.\n"
                "본인이 요청하지 않았다면 이 메일을 무시하세요. 비밀번호는 그대로 유지됩니다.\n"
            ),
        )

    # 가입되지 않은 주소인지 알려주지 않기 위해, 계정 유무와 상관없이 같은 화면을 보여준다
    return templates.TemplateResponse(
        request=request,
        name="auth/reset.html",
        context={
            "email": email_norm,
            "message": "가입된 계정이라면 재설정 코드를 메일로 보냈습니다.",
        },
    )


# ---------------------------
# 비밀번호 찾기 2단계: 코드 확인 -> 새 비밀번호 설정
# ---------------------------
@router.post("/reset", response_class=HTMLResponse)
async def reset_password(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
):
    email_norm = normalize_email(email)
    code = (code or "").strip()
    now = now_utc()

    def fail(msg: str, status_code: int = 400):
        return templates.TemplateResponse(
            request=request,
            name="auth/reset.html",
            context={"email": email_norm, "error": msg},
            status_code=status_code,
        )

    pending = password_resets_col.find_one(
        {"email": email_norm, "expires_at": {"$gt": now}}
    )
    if not pending:
        return fail("코드가 만료되었거나 없습니다. 다시 요청해 주세요.")

    if int(pending.get("tries", 0)) >= OTP_MAX_TRIES:
        password_resets_col.delete_one({"email": email_norm})
        return fail("시도 횟수를 초과했습니다. 처음부터 다시 요청해 주세요.", 429)

    if not verify_code(email_norm, code, pending.get("code_hash", "")):
        password_resets_col.update_one({"email": email_norm}, {"$inc": {"tries": 1}})
        return fail("인증 코드가 올바르지 않습니다.")

    # 코드가 맞았으니 비밀번호 정책을 확인한다 (코드는 아직 소모하지 않는다)
    if new_password != new_password2:
        return fail("새 비밀번호가 서로 일치하지 않습니다.")

    problem = password_problem(new_password)
    if problem:
        return fail(problem)

    user = users_col.find_one({"email": email_norm})
    if not user:
        password_resets_col.delete_one({"email": email_norm})
        return fail("계정을 찾을 수 없습니다.")

    users_col.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(new_password),
                "password_changed_at": now,
            }
        },
    )
    password_resets_col.delete_one({"email": email_norm})

    # 계정 복구 상황이므로 기존 세션은 전부 끊는다
    auth_col.delete_many({"kind": "session", "email": email_norm})

    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            "next": "/",
            "email": email_norm,
            "message": "비밀번호를 재설정했습니다. 새 비밀번호로 로그인해 주세요.",
        },
    )


@router.post("/logout")
async def logout(request: Request, next: str = "/"):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        auth_col.delete_many({"kind": "session", "session_id": sid})
    resp = RedirectResponse(url=_safe_next(next), status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
