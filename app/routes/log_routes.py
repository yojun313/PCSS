from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.core.templates import templates
from app.db import log_col
from app.libs.auth import _require_admin_or_redirect

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def admin_log_dashboard(request: Request):
    # 1. 관리자 권한 체크
    email, resp = _require_admin_or_redirect(request, "/admin/logs")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    # [분석 1] 날짜별 접속 현황 (전체 기록, 날짜 오름차순 정렬)
    daily_traffic_pipeline = [
        {
            # 날짜별로 그룹화 (UTC 기준 날짜)
            "$group": {
                "_id": {
                    "$dateToString": { "format": "%Y-%m-%d", "date": "$ts" }
                },
                "total_hits": { "$sum": 1 },          # 총 로그 수
                "unique_ips": { "$addToSet": "$ip" }, # 고유 IP 수 (방문자 수 근사치)
                "unique_emails": { "$addToSet": "$email" } # 로그인한 고유 유저 수
            }
        },
        {
            # 필요한 필드만 정리
            "$project": {
                "date": "$_id",
                "total_hits": 1,
                "visitor_count": { "$size": "$unique_ips" },   # IP 개수 세기
                "user_count": { "$size": "$unique_emails" },   # 이메일 개수 세기
                "_id": 0
            }
        },
        {
            # [요청사항] 날짜순 정렬 (오름차순)
            "$sort": { "date": 1 }
        }
    ]
    
    daily_stats = list(log_col.aggregate(daily_traffic_pipeline))

    # 차트용 데이터 리스트로 변환
    chart_dates = [item['date'] for item in daily_stats]
    chart_visitors = [item['visitor_count'] for item in daily_stats]
    chart_hits = [item['total_hits'] for item in daily_stats]


    # [분석 2] 로그 타입별 비율 (Pie Chart용)
    type_pipeline = [
        {"$group": {"_id": "$type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    type_stats = list(log_col.aggregate(type_pipeline))
    
    chart_types = [item['_id'] for item in type_stats]
    chart_type_counts = [item['count'] for item in type_stats]


    # [분석 3] 최근 로그 100개 (상세 테이블용)
    recent_logs = list(log_col.find().sort("ts", -1).limit(100))

    # 렌더링
    return templates.TemplateResponse(
        request=request,
        name="admin/log_dashboard.html",
        context={
            "email": email,
            
            # 차트 데이터 (Jinja2에서 tojson으로 쓰기 위해 전달)
            "chart_dates": chart_dates,
            "chart_visitors": chart_visitors,
            "chart_hits": chart_hits,
            
            "chart_types": chart_types,
            "chart_type_counts": chart_type_counts,
            
            # 상세 데이터
            "recent_logs": recent_logs,
            "daily_stats": daily_stats # 표로도 보여주기 위해 전달
        }
    )