import re
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from datetime import datetime, timezone

SUSPICIOUS_PATTERNS = [
    r"proc/self",
    r"\.aws",
    r"wp-config",
    r"config/database",
    r"config\.php",
    r"\.env",
    r"/etc/passwd",
    r"\.git/",
]

COMPILED = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]

_blocked_cache: set[str] = set()


def reload_blocked_cache():
    from app.db import blocked_ip_col

    global _blocked_cache
    _blocked_cache = {doc["ip"] for doc in blocked_ip_col.find({}, {"ip": 1})}


def is_suspicious(query_params: dict) -> bool:
    values = " ".join(str(v) for v in query_params.values())
    return any(pat.search(values) for pat in COMPILED)


class IPBlockerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.libs.logger import get_client_ip
        from app.db import blocked_ip_col

        ip = get_client_ip(request)

        # 캐시에 있으면 즉시 차단
        if ip in _blocked_cache:
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # 의심 쿼리 감지 → 캐시 + DB에 저장
        if is_suspicious(dict(request.query_params)):
            _blocked_cache.add(ip)
            blocked_ip_col.update_one(
                {"ip": ip},
                {
                    "$set": {
                        "ip": ip,
                        "blocked_at": datetime.now(timezone.utc),
                        "reason": dict(request.query_params),
                    }
                },
                upsert=True,
            )
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        return await call_next(request)
