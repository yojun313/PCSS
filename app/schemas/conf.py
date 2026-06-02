from pydantic import BaseModel, Field
from typing import List


class AddUrlRequest(BaseModel):
    url: str


class CreateConferenceRequest(BaseModel):
    kind: str
    name: str
    params: List[str] = Field(..., min_items=1)
    urls: List[str] = Field(default_factory=list)


class AddParamRequest(BaseModel):
    param: str


class AddParamsRequest(BaseModel):
    params: List[str] = Field(..., min_items=1)


class DeleteParamRequest(BaseModel):
    param: str
