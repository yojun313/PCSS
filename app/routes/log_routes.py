"""로그 분석 대시보드.

로그 문서는 미들웨어가 아니라 각 라우트가 직접 남깁니다:
  homepage      - 메인 페이지 방문 (path/query/referer 포함)
  search_start  - 검색 실행 (options: 학회 목록·연도 범위·확률 임곗값)
  subscription_* - 구독 관련 액션

여기서는 그 원본 로그를 여러 각도(일별·시간대별·유형·유입 경로·브라우저·
검색 학회)로 집계해 한 화면에 보여 줍니다. 로그가 수천 건 규모라 매 요청
집계해도 밀리초 단위입니다 - 캐시를 둘 이유가 없습니다.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.templates import templates
from app.db import log_col
from app.libs.auth import _require_admin_or_redirect

router = APIRouter()

# 하루/시간 경계는 한국 시간 기준 - UTC로 자르면 "오늘"이 아침 9시에 시작합니다.
_TZ = "Asia/Seoul"


def _parse_user_agent(ua: str) -> tuple[str, str]:
    """User-Agent -> (브라우저, 기기). "우리 사용자는 뭘로 들어오나"에 답할
    만큼만 구분합니다. Edge/삼성 UA에도 "Chrome"이, Chrome UA에도 "Safari"가
    들어 있어 구체적인 것부터 확인해야 합니다."""
    s = ua or ""
    low = s.lower()
    if not s:
        browser = "알 수 없음"
    elif any(
        t in low
        for t in ("bot", "spider", "crawler", "curl", "python", "wget", "httpclient")
    ):
        browser = "봇/스크립트"
    elif "edg/" in low:
        browser = "Edge"
    elif "samsungbrowser" in low:
        browser = "삼성 브라우저"
    elif "whale" in low:
        browser = "웨일"
    elif "firefox/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = "기타"
    device = (
        "모바일"
        if any(t in s for t in ("Mobile", "Android", "iPhone", "iPad"))
        else "데스크톱"
    )
    return browser, device


def _classify_referer(ref: str) -> str:
    """유입 경로. 자기 사이트 안에서의 이동은 "사이트 내 이동"으로 묶어
    "어디서 처음 들어왔나"가 검색엔진·직접 유입과 섞이지 않게 합니다."""
    r = (ref or "").lower()
    if not r:
        return "직접 접속"
    if "pcss." in r:
        return "사이트 내 이동"
    if "google." in r:
        return "Google"
    if "bing." in r:
        return "Bing"
    if "duckduckgo." in r:
        return "DuckDuckGo"
    if "naver." in r:
        return "네이버"
    if "postech." in r:
        return "POSTECH 사이트"
    return "기타 사이트"


_TYPE_LABELS = {
    "homepage": "페이지 방문",
    "search_start": "검색 실행",
}


def _type_label(t: str) -> str:
    if t in _TYPE_LABELS:
        return _TYPE_LABELS[t]
    if t and t.startswith("subscription"):
        return "구독 액션"
    return t or "기타"


@router.get("/", response_class=HTMLResponse)
async def admin_log_dashboard(request: Request, days: int = 30):
    email, status_or_resp = _require_admin_or_redirect(request, "/logs")
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        # 로그인은 했지만 관리자가 아님 - 예전 코드는 여기서 None을 반환해
        # 서버 오류가 났습니다.
        return templates.TemplateResponse(
            request=request,
            name="admin/access_denied.html",
            context={"error": "관리자 권한이 없습니다."},
            status_code=403,
        )

    days = min(max(days, 0), 365)  # 0 = 전체 기간
    now = datetime.now(timezone.utc)
    match = {"ts": {"$gte": now - timedelta(days=days)}} if days else {}

    total = log_col.count_documents(match)

    # ---- 일별 추이 (요청 수 · 순방문 IP · 로그인 사용자) ------------------
    daily = list(
        log_col.aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$ts",
                                "timezone": _TZ,
                            }
                        },
                        "hits": {"$sum": 1},
                        "ips": {"$addToSet": "$ip"},
                        "emails": {"$addToSet": "$email"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
    )
    daily_stats = [
        {
            "date": d["_id"],
            "hits": d["hits"],
            "visitors": len([i for i in d["ips"] if i]),
            "users": len([e for e in d["emails"] if e]),
        }
        for d in daily
    ]

    today_hits = daily_stats[-1]["hits"] if daily_stats else 0
    yesterday_hits = daily_stats[-2]["hits"] if len(daily_stats) >= 2 else 0

    # ---- 시간대별 (0~23시, KST) ------------------------------------------
    hourly = [0] * 24
    for r in log_col.aggregate(
        [
            {"$match": match},
            {
                "$group": {
                    "_id": {"$hour": {"date": "$ts", "timezone": _TZ}},
                    "n": {"$sum": 1},
                }
            },
        ]
    ):
        hourly[r["_id"]] = r["n"]

    # ---- 활동 유형 --------------------------------------------------------
    type_rows: dict[str, int] = {}
    for r in log_col.aggregate(
        [
            {"$match": match},
            {"$group": {"_id": "$type", "n": {"$sum": 1}}},
        ]
    ):
        label = _type_label(r["_id"])
        type_rows[label] = type_rows.get(label, 0) + r["n"]
    type_stats = sorted(
        ({"name": k, "count": v} for k, v in type_rows.items()),
        key=lambda x: -x["count"],
    )

    # ---- 순방문자/로그인 사용자 ------------------------------------------
    unique_ips = len(log_col.distinct("ip", {**match, "ip": {"$nin": [None, ""]}}))
    unique_users = len(
        log_col.distinct("email", {**match, "email": {"$nin": [None, ""]}})
    )
    search_count = log_col.count_documents({**match, "type": "search_start"})

    # ---- 유입 경로 / 브라우저 / 기기 --------------------------------------
    # 고유 문자열별로 묶어 받아 파이썬에서 분류합니다 - 고유 referer/UA는
    # 기껏 수백 개라 문서 전부를 가져오는 것보다 훨씬 쌉니다.
    referers: dict[str, int] = {}
    for r in log_col.aggregate(
        [
            {"$match": match},
            {"$group": {"_id": "$referer", "n": {"$sum": 1}}},
        ]
    ):
        k = _classify_referer(r["_id"] or "")
        referers[k] = referers.get(k, 0) + r["n"]
    referer_stats = sorted(
        ({"name": k, "count": v} for k, v in referers.items()),
        key=lambda x: -x["count"],
    )

    browsers: dict[str, int] = {}
    devices: dict[str, int] = {}
    for r in log_col.aggregate(
        [
            {"$match": match},
            {"$group": {"_id": "$user_agent", "n": {"$sum": 1}}},
        ]
    ):
        b, d = _parse_user_agent(r["_id"] or "")
        browsers[b] = browsers.get(b, 0) + r["n"]
        devices[d] = devices.get(d, 0) + r["n"]
    browser_stats = sorted(
        ({"name": k, "count": v} for k, v in browsers.items()),
        key=lambda x: -x["count"],
    )
    device_stats = sorted(
        ({"name": k, "count": v} for k, v in devices.items()),
        key=lambda x: -x["count"],
    )

    # ---- 활동 상위 사용자 -------------------------------------------------
    kst = timezone(timedelta(hours=9))

    def _kst(ts):
        if not ts:
            return "-"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(kst).strftime("%m-%d %H:%M")

    top_users = [
        {"email": r["_id"], "count": r["n"], "last": _kst(r["last"])}
        for r in log_col.aggregate(
            [
                {"$match": {**match, "email": {"$nin": [None, ""]}}},
                {
                    "$group": {
                        "_id": "$email",
                        "n": {"$sum": 1},
                        "last": {"$max": "$ts"},
                    }
                },
                {"$sort": {"n": -1}},
                {"$limit": 8},
            ]
        )
    ]

    # ---- 인기 검색 학회·연도 (search_start.options) -----------------------
    conf_counts: dict[str, int] = {}
    year_counts: dict[int, int] = {}
    for doc in log_col.find({**match, "type": "search_start"}, {"options": 1}):
        opts = doc.get("options") or {}
        if not isinstance(opts, dict):
            continue
        for conf in opts.get("selectedConferences") or []:
            conf_counts[str(conf)] = conf_counts.get(str(conf), 0) + 1
        try:
            sy, ey = int(opts.get("startyear")), int(opts.get("endyear"))
            for y in range(sy, min(ey, sy + 50) + 1):
                year_counts[y] = year_counts.get(y, 0) + 1
        except TypeError, ValueError:
            pass
    top_confs = sorted(
        ({"name": k, "count": v} for k, v in conf_counts.items()),
        key=lambda x: -x["count"],
    )[:12]
    year_stats = [{"year": y, "count": year_counts[y]} for y in sorted(year_counts)]

    # ---- 최근 로그 --------------------------------------------------------
    recent_logs = []
    for log in log_col.find(match).sort("ts", -1).limit(100):
        ts = log.get("ts")
        detail = []
        if log.get("path"):
            detail.append(str(log["path"]))
        if log.get("query") and log["query"] not in ({}, "{}"):
            detail.append(f"query={log['query']}")
        if log.get("options"):
            detail.append(f"options={log['options']}")
        recent_logs.append(
            {
                "ts": _kst(ts) if ts else "-",
                "type": _type_label(log.get("type")),
                "raw_type": log.get("type") or "",
                "ip": log.get("ip") or "-",
                "email": log.get("email") or "",
                "browser": _parse_user_agent(log.get("user_agent") or "")[0],
                "detail": " · ".join(detail)[:180],
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="admin/log_dashboard.html",
        context={
            "email": email,
            "days": days,
            "total": total,
            "today_hits": today_hits,
            "yesterday_hits": yesterday_hits,
            "unique_ips": unique_ips,
            "unique_users": unique_users,
            "search_count": search_count,
            "daily_stats": daily_stats,
            "hourly": hourly,
            "type_stats": type_stats,
            "referer_stats": referer_stats,
            "browser_stats": browser_stats,
            "device_stats": device_stats,
            "top_users": top_users,
            "top_confs": top_confs,
            "year_stats": year_stats,
            "recent_logs": recent_logs,
        },
    )
