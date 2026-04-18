from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
import traceback
from asgi_csrf import asgi_csrf
import os
from dotenv import load_dotenv

load_dotenv()

ENABLE_ACCESS_CONTROL = os.getenv("ENABLE_ACCESS_CONTROL", "true").lower() == "true"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PCSS API", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://pcss.r-e.kr",
        "https://pcss.r-e.kr",
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
        # 1. 접근 제어가 비활성화되어 있으면 모든 검증 건너빰
        if not ENABLE_ACCESS_CONTROL:
            return await call_next(request)
        
        # 2. 기본 예외 경로 처리
        if request.url.path.startswith(("/static", "/auth", "/favicon.ico", "/docs", "/openapi.json")):
            return await call_next(request)
        
        # 3. IP 화이트리스트 체크
        ip = get_client_ip(request)
        is_internal = ip.startswith("141.223.") or ip in ["127.0.0.1", "::1"]

        if is_internal:
            return await call_next(request)

        # 4. 세션 로그인 체크
        email = get_session_email(request)
        if email:
            return await call_next(request)

        # 모두 해당 안 되면 로그인 페이지로 리다이렉트
        return RedirectResponse(url=f"/auth/login?next={request.url.path}", status_code=302)

# 미들웨어 등록
app.add_middleware(AccessControlMiddleware)

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
        "errors/404.html",  # 미리 만들어둔 404 템플릿 경로
        {
            "request": request,
            "error_message": "" 
        },
        status_code=404
    )
    
@app.exception_handler(500)
async def custom_500_handler(request: Request, exc: Exception):
    try:
        ip = get_client_ip(request)
    
        error_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        # DB에 에러 로그 저장
        errors_col.insert_one({
            "ts": datetime.now(timezone.utc),
            "type": "server_error",
            "ip": ip,
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "error_message": str(exc),
            "traceback": error_trace, # 디버깅용 상세 정보
            "user_agent": request.headers.get("user-agent", ""),
        })
    except Exception as log_error:
        print(f"Failed to log 500 error to DB: {log_error}")

    return templates.TemplateResponse(
        "errors/500.html",
        {
            "request": request,
            "error_message": "서버 내부에서 오류가 발생했습니다. 관리자에게 문의하거나 잠시 후 다시 시도해주세요."
        },
        status_code=500
    )

app = asgi_csrf(
    app, 
    signing_secret=os.getenv("SUB_AUTH_SECRET", "fallback-secret-key-for-dev"),
    cookie_name="csrftoken",
    cookie_samesite="Lax",
    always_set_cookie=True,
    cookie_secure=True
)