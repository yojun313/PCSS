from fastapi import APIRouter, BackgroundTasks
from app.schemas.search import SearchRequest, SearchResponse
from app.services.pcssearch import PCSSEARCH

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
