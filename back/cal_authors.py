import requests
import os
import re
import json
from pymongo import MongoClient
import datetime

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "pcss"
COLLECTION_NAME = "llm_names"

def calculate_author():
    
    mongo_client = MongoClient(MONGO_URI)
    mongo_db = mongo_client[DB_NAME]
    mongo_col = mongo_db[COLLECTION_NAME]
    
    name_dict = {
        doc["name"]: doc["score"]
        for doc in mongo_col.find({}, {"_id": 0, "name": 1, "score": 1})
    }
    
    def llm_api_answer(query, model):
        # 전송할 데이터
        data = {
            "model": model,
            "prompt": query
        }

        try:
            # POST 요청 보내기
            response = requests.post(api_url, json=data)

            # 응답 확인
            if response.status_code == 200:
                result = response.json()['response']
                result = result.replace('<think>', '').replace('</think>', '').replace('\n\n', '')
                return result
            else:
                return False

        except requests.exceptions.RequestException as e:
            return False
    
    def single_name_llm(name, llm_model):        
        result = llm_api_answer(
            query = f"Express the likelihood of this {name} being Korean using only a number between 0~1. You need to say number only",
            model = llm_model
        )
        if result is False:
            return False

        # 🔹 숫자만 추출 (지수 표기법 방지)
        match = re.findall(r"\d+\.\d+|\d+", result)
        if not match:
            return "0.0"  # 예외 처리: 결과가 없을 경우 기본값

        value = float(match[0])  # 🔹 문자열을 float으로 변환

        # 🔹 숫자 범위 고정 (0.0 ~ 1.0)
        value = max(0.0, min(1.0, value))

        # 🔹 소수점 1자리까지 포맷팅
        formatted_value = "{:.1f}".format(value)

        mongo_col.update_one(
            {"name": name},
            {"$set": {"score": formatted_value, "updated_at": datetime.utcnow()}},
            upsert=True
        )

        return formatted_value  # 🔹 결과 반환 (0.0 ~ 1.0)
    
    LLM_SERVER = '141.223.16.196'
    PORT = "8089"
    api_url = f"http://{LLM_SERVER}:{PORT}/api/process"
    model = 'llama3.3:70b-instruct-q8_0'
    
    file_path = os.path.join(os.path.dirname(__file__), 'data', 'all_authors.json')
    # JSON 파일 읽기
    with open(file_path, 'r', encoding='utf-8') as f:
        names = json.load(f)  # JSON 데이터를 리스트로 불러옴    
    
    # 개행 문자 제거
    names = [name.strip() for name in names]
    names = list(set(names))
    names = [name for name in names if name not in name_dict]
    
    total = len(names)
    
    counter = 0  # 처리한 이름 개수를 추적
    for name in names:
        result = single_name_llm(name, model)
        print(f"[{counter}/{total}] {name} : {result}")
        counter += 1

if __name__ == "__main__":
    calculate_author()