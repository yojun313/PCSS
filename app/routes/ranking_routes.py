from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.templates import templates
from app.services.insights_service import (
    RANKING_METRICS,
    compute_rankings,
    ranking_kinds,
)

router = APIRouter()

PERIODS = (3, 5, 10)


@router.get("", response_class=HTMLResponse)
async def rankings_page(
    request: Request,
    kind: str = "all",
    years: int = Query(5),
    metric: str = Query("first"),
    prob: float = Query(0.7, ge=0.0, le=1.0),
    newcomer: bool = False,
):
    kinds = ranking_kinds()
    if kind != "all" and kind not in kinds:
        kind = "all"
    if years not in PERIODS:
        years = 5
    if metric not in RANKING_METRICS:
        metric = "first"

    end = datetime.now().year
    start = end - years + 1
    data = await compute_rankings(kind, start, end, metric, prob, newcomer)

    return templates.TemplateResponse(
        request=request,
        name="search/rankings.html",
        context={
            "kinds": kinds,
            "periods": PERIODS,
            "metrics": {k: v[0] for k, v in RANKING_METRICS.items()},
            "kind": kind,
            "years": years,
            "metric": metric,
            "metric_label": RANKING_METRICS[metric][0],
            "prob": prob,
            "newcomer": newcomer,
            "start": start,
            "end": end,
            **data,
        },
    )
