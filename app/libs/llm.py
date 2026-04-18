import requests
import re
from app.config import LLM_URL
from app.data import name_dict
from app.db import name_col
from app.config import NAME_CACHE
import math
import os
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

CUSTOM_TOKEN = os.getenv("CUSTOM_TOKEN")

def get_headers():    
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CUSTOM_TOKEN}",
    }
    
# # 모델 조회
# model_resp = requests.get(f"{LLM_URL}/models", headers=get_headers())
# model_resp.raise_for_status()
# LLM_MODEL = model_resp.json()["data"][0]["id"]

def single_name_llm(name):
    if NAME_CACHE:
        try:
            return round(name_dict[name], 1)
        except KeyError:
            pass
    
    result = llm_api_answer(
        query = f"Express the likelihood of this {name} being Korean using only a number between 0~1. You need to say number only",
        model = LLM_MODEL
    )
    
    # 숫자만 추출 (지수 표기법 방지)
    match = re.findall(r"\d+\.\d+|\d+", str(result))
    if not match:
        score = 0.0
    else:
        try:
            score = float(match[0])
        except Exception:
            score = 0.0
            
    score = max(0.0, min(1.0, score))
    score = float(f"{score:.1f}")
    name_dict[name] = score
    
    if NAME_CACHE:
        now = datetime.now(timezone.utc)
        name_col.update_one(
            {"name": name},
            {
                "$set": {"score": score},
                "$setOnInsert": {"created_at": now},
                "$currentDate": {"updated_at": True},
            },
            upsert=True
        )
    return score 

def llm_api_answer(query, model):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an expert in determining the likelihood that a given name is Korean."},
            {"role": "user", "content": query},
        ],
        "temperature": 0.7,
        "max_tokens": 100,
    }
    response = requests.post(
        f"{LLM_URL}/chat/completions",
        json=payload,
        headers=get_headers(),
        timeout=60,
    )
    result = response.json()
    return result["choices"][0]["message"]["content"]

def get_name_score(name):
    score = name_dict.get(name)
    if score is None:
        return None
    # 소수점 첫째 자리까지 "내림" 후 항상 한 자리까지 표현
    return f"{math.floor(score * 10) / 10:.1f}"