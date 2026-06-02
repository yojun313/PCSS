# app/libs/auth.py
import hashlib
import os
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import RedirectResponse
from app.db import auth_col, admin_col

SESSION_COOKIE = "pcss_sub_session"


def now_utc():
    return datetime.now(timezone.utc)


def auth_secret() -> str:
    # 운영에서는 기본값 쓰지 말고 필수로 두는 걸 추천
    return os.getenv("SUB_AUTH_SECRET", "dev-secret-change-me")


def hash_code(email: str, code: str) -> str:
    raw = f"{email}|{code}|{auth_secret()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_session_email(request: Request) -> str | None:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    now = now_utc()
    doc = auth_col.find_one(
        {"kind": "session", "session_id": sid, "expires_at": {"$gt": now}},
        {"_id": 0, "email": 1},
    )
    return (doc or {}).get("email")


def require_login(request: Request) -> str | None:
    # 일단 현재 스타일대로 None 반환
    return get_session_email(request)


def _safe_next(next_url: str) -> str:
    # 오픈 리다이렉트 방지: 내부 경로만 허용
    if not next_url or not isinstance(next_url, str):
        return "/"
    if not next_url.startswith("/"):
        return "/"
    if next_url.startswith("//"):
        return "/"
    return next_url


def _is_admin(email: str) -> bool:
    if not email:
        return False
    return admin_col.find_one({"email": email.lower()}, {"_id": 1}) is not None


def _require_admin_or_redirect(request: Request, next_path: str):
    email = require_login(request)
    if not email:
        return None, RedirectResponse(
            f"/auth/login?next={_safe_next(next_path)}", status_code=302
        )
    if not _is_admin(email):
        return email, None  # 이메일은 있으나 권한 없음
    return email, "ok"
