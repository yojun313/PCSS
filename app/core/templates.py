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


def _session_email(request) -> str | None:
    """공용 사이드바의 계정 영역용. 페이지마다 email 을 넘기지 않아도 되게 한다."""
    from app.libs.auth import get_session_email

    try:
        return get_session_email(request)
    except Exception:
        return None


def _session_is_admin(email) -> bool:
    from app.libs.auth import _is_admin

    try:
        return _is_admin(email)
    except Exception:
        return False


templates.env.globals["session_email"] = _session_email
templates.env.globals["session_is_admin"] = _session_is_admin
