from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, AnyHttpUrl
import re
import httpx
from bs4 import BeautifulSoup
import os
import pandas as pd
from fastapi.middleware.cors import CORSMiddleware


conf_df = pd.read_csv(os.path.join(os.path.dirname(__file__), 'data', 'conf.csv'))
conf_param_list = dict(zip(conf_df["param"], conf_df["conference"]))


app = FastAPI(title="PCSS API", version="1.0.0")


class AuthorStatsRequest(BaseModel):
    target_author: str
    url: AnyHttpUrl
    max_retry: int = 10                           # publ-list가 보일 때까지 재시도 횟수
    timeout_sec: float = 15.0                     # HTTP 타임아웃
    include_papers: bool = False                  # True면 논문 목록도 응답에 포함


class AuthorStatsResponse(BaseModel):
    stats: str   # "(first,first_or_second,last,co)"
    total: int
    papers: Optional[List[Dict[str, Any]]] = None


async def fetch_html(url: str, timeout_sec: float) -> str:
    async with httpx.AsyncClient(timeout=timeout_sec, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def compute_author_stats(
    html: str,
    target_author: str,
    max_retry: int = 10
) -> Dict[str, Any]:
    stats = {
        "first_author": 0,
        "first_or_second_author": 0,
        "last_author": 0,
        "co_author": 0,
    }

    soup = BeautifulSoup(html, "lxml")

    trynum = 1
    publ_lists = soup.find_all("ul", class_="publ-list")
    while (publ_lists is None or len(publ_lists) == 0) and trynum < max_retry:
        publ_lists = soup.find_all("ul", class_="publ-list")
        trynum += 1

    papers: List[Dict[str, Any]] = []

    for publ_list in publ_lists:

        current_year = None

        # publ-list 내부의 <li>를 순서대로 접근
        for li in publ_list.find_all("li", recursive=False):

            # 1) 연도 업데이트
            if "year" in li.get("class", []):
                current_year = li.get_text(strip=True)
                continue

            # 2) entry 처리
            if not re.search(r"entry", " ".join(li.get("class", []))):
                continue  # year도 entry도 아닌 li는 무시

            if current_year is None:
                # year 이전 entry는 무시
                continue

            # -------- conf 추출 --------
            conf = None
            if li.has_attr("id"):
                parts = li["id"].split("/")
                if len(parts) > 1:
                    conf = parts[1]

            # conf 필터링
            if conf_param_list is not None:
                if conf is None or conf not in conf_param_list:
                    continue

            conf = conf_param_list[conf]

            # -------- title --------
            title_tag = li.find("span", class_="title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            # -------- authors --------
            middle = li.find("cite", class_="data tts-content")
            if not middle:
                middle = li  # fallback

            authors = middle.select('span[itemprop="name"]:not(.title)')
            author_list = [a.get_text(strip=True) for a in authors]

            if len(author_list) > 0:
                # 기존 코드의 마지막 요소 제거 로직 유지
                author_list.pop()

            if not author_list:
                continue

            papers.append(
                {
                    "title": title,
                    "authors": author_list,
                    "conf": f"{conf} {current_year}",
                }
            )
            
    # 통계 집계
    paperCnt = 0
    for paper in papers:
        authors = paper["authors"]
        if target_author in authors:
            paperCnt += 1
            if len(authors) >= 1 and authors[0] == target_author:
                stats["first_author"] += 1
                stats["first_or_second_author"] += 1  # 1저자는 1or2 저자에도 포함
            elif len(authors) > 1 and authors[1] == target_author:
                stats["first_or_second_author"] += 1
            elif len(authors) >= 1 and authors[-1] == target_author:
                stats["last_author"] += 1

            stats["co_author"] += 1

    result = {
        "stats": f"({stats['first_author']},{stats['first_or_second_author']},{stats['last_author']},{stats['co_author']})",
        "total": paperCnt,
        "papers": papers,
    }
    return result


@app.post("/author-stats", response_model=AuthorStatsResponse)
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://pcss.r-e.kr",
        "https://pcss.r-e.kr",
        "http://pcss.r-e.kr:3000",
        "http://pcss.r-e.kr:8000",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    # reload를 쓰려면 반드시 "모듈:앱" 문자열로!
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )