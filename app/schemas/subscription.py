from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional
from datetime import datetime


class SubscriptionCreateRequest(BaseModel):
    email: EmailStr
    conferences: List[str] = Field(..., min_length=1)
    # 4개의 옵션만 일단 지원
    options: List[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    threshold: float = 0.8
    is_enabled: bool = True


class SubscriptionUpdateRequest(BaseModel):
    email: EmailStr
    manage_token: str

    conferences: Optional[List[str]] = None
    options: Optional[List[int]] = None
    threshold: Optional[float] = None
    is_enabled: Optional[bool] = None


class SubscriptionUnsubscribeRequest(BaseModel):
    email: EmailStr
    manage_token: str
