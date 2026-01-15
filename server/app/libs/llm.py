import requests
import re
from app.config import LLM_URL, LLM_MODEL
from app.data import name_dict
from app.db import name_col
import math
import os
from dotenv import load_dotenv

load_dotenv()

CUSTOM_TOKEN = os.getenv("CUSTOM_TOKEN")

def get_headers():    
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CUSTOM_TOKEN}",
    }
    
def single_name_llm(name):
    try:
        return round(name_dict[name], 1)
    except KeyError:
        pass
    
    result = llm_api_answer(
        query = f"Express the likelihood of this {name} being Korean using only a number between 0~1. You need to say number only",
        model = LLM_MODEL
    )

    # 숫자만 추출 (지수 표기법 방지)
    match = re.findall(r"\d+\.\d+|\d+", result)
    if not match:
        return "0.0"  # 예외 처리: 결과가 없을 경우 기본값

    value = float(match[0])  # 숫자 문자열을 float으로 변환

    # 숫자 범위 고정 (0.0 ~ 1.0)
    value = max(0.0, min(1.0, value))

    # 소수점 1자리까지 포맷팅
    formatted_value = "{:.1f}".format(value)

    name_dict[name] = formatted_value
    name_col.update_one(
        {"name": name},
        {"$set": {"score": formatted_value}},
        upsert=True
    )

    return formatted_value  # 결과 반환 (0.0 ~ 1.0)


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
        LLM_URL,
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