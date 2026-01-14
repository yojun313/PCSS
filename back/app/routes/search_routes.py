from fastapi import APIRouter, BackgroundTasks
from app.schemas.search import SearchRequest, SearchResponse, AuthorStatsRequest, AuthorStatsResponse
from fastapi import HTTPException
from app.services.search_service import PCSSEARCH, compute_author_stats, fetch_html
import httpx


router = APIRouter()

@router.post("/search", response_model=SearchResponse)
async def run_search(req: SearchRequest):
    pcs = PCSSEARCH(
        option=req.option,
        threshold=req.uncertainty,
        startyear=req.startyear,
        endyear=req.endyear,
        countOption=req.countOption
    )

    result_path = await pcs.run(req.selectedConferences)
    return {"result_path": result_path}

@router.post("/author-stats", response_model=AuthorStatsResponse)
async def author_stats(payload: AuthorStatsRequest):
    try:
        html = await fetch_html(str(payload.url), payload.timeout_sec)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch url: {e}") from e

    result = compute_author_stats(
        html=html,
        target_author=payload.target_author,
        max_retry=payload.max_retry,
    )

    # include_papers가 아니면 papers 필드 제거
    if not payload.include_papers:
        result.pop("papers", None)

    return result
