from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone

from app.core.templates import templates
from app.db import log_col, admin_col
from app.libs.logger import get_client_ip
from app.libs.auth import get_session_email

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    email = get_session_email(request)  # 없으면 None

    is_admin = False
    if email:
        # admin 컬렉션에 해당 이메일이 있는지 확인
        if admin_col.find_one({"email": email}):
            is_admin = True
    try:
        ip = get_client_ip(request)
        if ip not in ["127.0.0.1", "::1"]:
            log_col.insert_one(
                {
                    "ts": datetime.now(timezone.utc),
                    "type": "homepage",
                    "ip": ip,
                    "email": email,
                    "method": request.method,
                    "path": request.url.path,
                    "query": dict(request.query_params),
                    "user_agent": request.headers.get("user-agent", ""),
                    "referer": request.headers.get("referer", ""),
                }
            )
    except Exception:
        print("Failed to log homepage visit event")

    return templates.TemplateResponse(
        request=request,
        name="homepage.html",
        context={"email": email, "is_admin": is_admin},
    )
