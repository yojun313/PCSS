from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, AnyHttpUrl

class SearchRequest(BaseModel):
    option: int
    uncertainty: float
    startyear: int
    endyear: int
    selectedConferences: List[str]
    countOption: bool = False


class SearchResponse(BaseModel):
    result_path: str


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
