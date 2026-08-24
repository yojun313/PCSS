# app/libs/auth.py
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse
from app.db import auth_col, admin_col

SESSION_COOKIE = "pcss_sub_session"
SESSION_DAYS = 14

# 가입 가능한 이메일 도메인 (POSTECH 계정만 허용)
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "postech.ac.kr")

# 가입 인증 코드 정책
OTP_TTL_MINUTES = 10
OTP_MAX_TRIES = 5

PASSWORD_MIN_LENGTH = 8


def now_utc():
    return datetime.now(timezone.utc)


def auth_secret() -> str:
    # 운영에서는 기본값 쓰지 말고 필수로 두는 걸 추천
    return os.getenv("SUB_AUTH_SECRET", "dev-secret-change-me")


def hash_code(email: str, code: str) -> str:
    raw = f"{email}|{code}|{auth_secret()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_code(email: str, code: str, expected_hash: str) -> bool:
    if not expected_hash:
        return False
    return secrets.compare_digest(expected_hash, hash_code(email, code))


def generate_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


# ---------------------------
# 이메일 정규화 / 도메인 제한
# ---------------------------
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_allowed_email(email: str) -> bool:
    return normalize_email(email).endswith(f"@{ALLOWED_EMAIL_DOMAIN}")


# ---------------------------
# 비밀번호 해싱 (bcrypt)
# ---------------------------
def _pw_bytes(password: str) -> bytes:
    # bcrypt는 72바이트를 넘는 입력을 거부하므로 해싱/검증에서 동일하게 잘라 쓴다
    return (password or "").encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_pw_bytes(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    """비밀번호 정책 위반 사유를 돌려준다. 문제 없으면 None."""
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        return f"비밀번호는 최소 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
    return None


# ---------------------------
# 세션 생성
# ---------------------------
def create_session(email: str, ip: str) -> tuple[str, int]:
    """세션 문서를 만들고 (session_id, max_age초)를 돌려준다."""
    now = now_utc()
    session_id = secrets.token_urlsafe(32)
    auth_col.insert_one(
        {
            "kind": "session",
            "session_id": session_id,
            "email": email,
            "expires_at": now + timedelta(days=SESSION_DAYS),
            "created_at": now,
            "ip": ip,
        }
    )
    return session_id, SESSION_DAYS * 24 * 3600


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
