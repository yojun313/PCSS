from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templates import templates
from app.services.conf_stats_service import build_overview

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def stats_page(request: Request):
    data = await build_overview()
    return templates.TemplateResponse(
        request=request, name="search/conf_stats.html", context={"data": data}
    )
