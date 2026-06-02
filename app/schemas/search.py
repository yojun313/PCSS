from pydantic import BaseModel
from typing import List
from pydantic import BaseModel


class SearchRequest(BaseModel):
    options: List[int]
    probability: float
    startyear: int
    endyear: int
    selectedConferences: List[str]
    countOption: bool = False
