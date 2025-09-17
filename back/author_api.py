from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, AnyHttpUrl
import re
import httpx
from bs4 import BeautifulSoup
import os
import pandas as pd

conf_df = pd.read_csv(os.path.join(os.path.dirname(__file__), 'data', 'conf.csv'))
conf_param_list = conf_df['param'].tolist()

app = FastAPI(title="Author Stats API", version="1.0.0")


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
    """
    질문에 주신 크롤링/집계 로직을 함수형으로 재작성.
    """
    stats = {
        "first_author": 0,
        "first_or_second_author": 0,
        "last_author": 0,
        "co_author": 0,
    }

    soup = BeautifulSoup(html, "lxml")

    # publ-list가 로드될 때까지 재시도 (질문 코드 호환)
    trynum = 1
    publ_lists = soup.find_all("ul", class_="publ-list")
    while (publ_lists is None or len(publ_lists) == 0) and trynum < max_retry:
        # BeautifulSoup은 정적 파싱이라 같은 html에서 반복해도 결과가 바뀌지 않지만,
        # 원본 코드의 구조를 최대한 유지합니다.
        publ_lists = soup.find_all("ul", class_="publ-list")
        trynum += 1

    papers: List[Dict[str, Any]] = []

    for publ_list in publ_lists or []:
        entries = publ_list.find_all("li", class_=re.compile(r"entry"))
        for paper in entries:
            # ID에서 conf 키 추출
            conf = None
            if paper.has_attr("id"):
                parts = paper["id"].split("/")
                if len(parts) > 1:
                    conf = parts[1]

            # conf 필터가 있으면 pass/continue 결정
            if conf_param_list is not None:
                if conf is None or conf not in conf_param_list:
                    continue

            title_tag = paper.find("span", class_="title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            # 질문 코드의 방식: authors를 cite.data.tts-content 아래에서 span[itemprop=name]로 추출,
            # 마지막 요소는 제거(pop) (사이트마다 마지막이 '…' 등인 경우가 있어 보임)
            middle = paper.find("cite", class_="data tts-content")
            if not middle:
                # 구조가 다를 때를 대비해 fallback: paper 내부에서 직접 탐색
                middle = paper

            authors = middle.select('span[itemprop="name"]:not(.title)')
            author_list = [a.get_text(strip=True) for a in authors]

            if len(author_list) > 0:
                # 원본 코드 호환: 마지막 요소 제거
                author_list.pop()

            if not author_list:
                continue

            papers.append(
                {
                    "title": title,
                    "authors": author_list,
                    "conf": conf,
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
