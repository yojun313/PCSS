from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import status
from app.core.paths import STATIC_DIR, TEMPLATES_DIR
from app.routes.author_routes import router as author_router
from app.routes.home_routes import router as home_router
from app.routes.subscription_routes import router as subscription_router
from app.routes.auth_routes import router as auth_router
from app.routes.conf_routes import router as conf_router
from app.routes.search_routes import router as search_router
from app.routes.admin_routes import router as admin_router
from app.routes.board_routes import router as board_router
from app.routes.log_routes import router as log_router
from app.db.mongo import get_mongo_client
from app.db import errors_col
from app.libs.logger import get_client_ip
from app.libs.auth import get_session_email
from datetime import datetime, timezone
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from app.core.templates import templates
from app.middleware.ip_blocker import IPBlockerMiddleware, reload_blocked_cache
import traceback
from asgi_csrf import asgi_csrf
import os
from dotenv import load_dotenv

load_dotenv()

ENABLE_ACCESS_CONTROL = os.getenv("ENABLE_ACCESS_CONTROL", "true").lower() == "true"
ENABLE_IP_BLOCKER = os.getenv("ENABLE_IP_BLOCKER", "true").lower() == "true"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PCSS API", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://pcss.postech.ac.kr",
        "https://pcss.postech.ac.kr",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    await get_mongo_client()
    if ENABLE_IP_BLOCKER:
        from app.db import blocked_ip_col
        blocked_ip_col.create_index("ip", unique=True)
        reload_blocked_cache()  # DB에서 기존 차단 IP 캐시에 로드


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for error in errors:
        if "input" in error:
            del error["input"]

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


class AccessControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ENABLE_ACCESS_CONTROL:
            return await call_next(request)

        if request.url.path.startswith(
            ("/static", "/auth", "/favicon.ico", "/docs", "/openapi.json")
        ):
            return await call_next(request)

        ip = get_client_ip(request)
        is_internal = ip.startswith("141.223.") or ip in ["127.0.0.1", "::1"]

        if is_internal:
            return await call_next(request)

        email = get_session_email(request)
        if email:
            return await call_next(request)

        return RedirectResponse(
            url=f"/auth/login?next={request.url.path}", status_code=302
        )


# IPBlockerMiddleware가 먼저 실행되도록 AccessControl을 나중에 등록
app.add_middleware(AccessControlMiddleware)
if ENABLE_IP_BLOCKER:
    app.add_middleware(IPBlockerMiddleware)

app.include_router(home_router)
app.include_router(conf_router, prefix="/conferences")
app.include_router(author_router, prefix="/author")
app.include_router(subscription_router, prefix="/subscriptions")
app.include_router(auth_router, prefix="/auth")
app.include_router(search_router, prefix="/api/search")
app.include_router(admin_router, prefix="/admin")
app.include_router(board_router, prefix="/board")
app.include_router(log_router, prefix="/logs")


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    return templates.TemplateResponse(
        request=request,
        name="errors/404.html",
        context={"error_message": "요청하신 페이지를 찾을 수 없습니다."},
        status_code=404,
    )


@app.exception_handler(500)
async def custom_500_handler(request: Request, exc: Exception):
    try:
        ip = get_client_ip(request)
        error_trace = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        errors_col.insert_one(
            {
                "ts": datetime.now(timezone.utc),
                "type": "server_error",
                "ip": ip,
                "method": request.method,
                "path": request.url.path,
                "query_params": dict(request.query_params),
                "error_message": str(exc),
                "traceback": error_trace,
                "user_agent": request.headers.get("user-agent", ""),
            }
        )
    except Exception as log_error:
        print(f"Failed to log 500 error to DB: {log_error}")

    return templates.TemplateResponse(
        request=request,
        name="errors/500.html",
        context={
            "error_message": "서버 내부에서 오류가 발생했습니다. 관리자에게 문의하거나 잠시 후 다시 시도해주세요."
        },
        status_code=500,
    )


app = asgi_csrf(
    app,
    signing_secret=os.getenv("SUB_AUTH_SECRET", "fallback-secret-key-for-dev"),
    cookie_name="csrftoken",
    cookie_samesite="Lax",
    always_set_cookie=True,
    cookie_secure=True,
)