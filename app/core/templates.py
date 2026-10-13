from fastapi.templating import Jinja2Templates
from app.core.paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _admin_pending_requests() -> int:
    """관리자 헤더 메뉴의 '이름 요청' 옆 숫자. 컬렉션이 작아 매번 세도 된다."""
    from app.db import (
        req_korean_col,
    )  # app.db 가 templates 보다 늦게 준비될 수 있어 지연 import

    try:
        return req_korean_col.count_documents({})
    except Exception:
        return 0


templates.env.globals["admin_pending_requests"] = _admin_pending_requests
