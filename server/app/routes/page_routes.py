from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi import Form
from app.core.templates import templates
from app.services.search_service import compute_author_stats, fetch_html

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("homepage.html", {"request": request})

@router.post("/author-stats/page", response_class=HTMLResponse)
async def author_stats_page(
    request: Request,
    target_author: str = Form(...),
    url: str = Form(...),
    include_papers: bool = Form(True),
):
    html = await fetch_html(url)

    result = compute_author_stats(
        html=html,
        target_author=target_author,
        max_retry=3,
    )

    if not include_papers:
        result.pop("papers", None)
        
    return templates.TemplateResponse(
        "author_stats.html",   
        {
            "request": request,
            "name": target_author,
            "url": url,
            "stats": result.get("stats", []),
            "total": result.get("total", 0),
            "papers": result.get("papers", []),
        },
    )
