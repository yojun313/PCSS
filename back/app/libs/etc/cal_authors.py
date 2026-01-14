import requests
import os
import re
import json
from pymongo import MongoClient
from datetime import datetime, timezone
from dotenv import load_dotenv
from pymongo import UpdateOne

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "pcss"
COLLECTION_NAME = "llm_names"

LLM_SERVER = "141.223.16.196"
PORT = "8089"
API_SINGLE = f"http://{LLM_SERVER}:{PORT}/api/process"
API_BATCH = f"http://{LLM_SERVER}:{PORT}/api/batch"
MODEL = "llama3.3:70b-instruct-q8_0"

def update_score(name_col, name, score):
    name_col.update_one(
        {"name": name},
        {"$set": {"score": round(score, 1)}},
        upsert=True,
    )

def parse_number(text):
    match = re.search(r"\d+\.\d+|\d+", text)
    if not match:
        return 0.0
    val = float(match.group())
    return max(0.0, min(1.0, val))

def send_batch(names):
    prompts = [
        f"Express the likelihood of this {n} being Korean using only a number between 0~1. You need to say number only"
        for n in names
    ]
    data = {"model": MODEL, "prompts": prompts}
    try:
        resp = requests.post(API_BATCH, json=data, timeout=60)
        if resp.status_code == 200:
            return resp.json()["responses"]
        return []
    except requests.exceptions.RequestException:
        return []

def calculate_author(batch_size):
    mongo_client = MongoClient(MONGO_URI)
    mongo_db = mongo_client[DB_NAME]
    name_col = mongo_db[COLLECTION_NAME]

    # DB에 이미 저장된 이름: score만 뽑기
    name_dict = {
        doc["name"]: doc["score"]
        for doc in name_col.find({}, {"_id": 0, "name": 1, "score": 1})
    }

    file_path = os.path.join(os.path.dirname(__file__), "data", "all_authors.json")
    with open(file_path, "r", encoding="utf-8") as f:
        names = json.load(f)

    names = [n.strip() for n in names]
    names = list(set(names))
    names = [n for n in names if n not in name_dict]

    total = len(names)
    counter = 0

    for i in range(0, total, batch_size):
        sub = names[i : i + batch_size]
        responses = send_batch(sub)
        for name, text in zip(sub, responses):
            score = parse_number(text)
            update_score(name_col, name, score)
            print(f"[{counter}/{total}] {name} : {score}")
            counter += 1
            
def add_author(batch_size: int = 50000):
    """JSON의 name/results를 MongoDB에 빠르게 upsert"""
    mongo_client = MongoClient(MONGO_URI)
    mongo_db = mongo_client[DB_NAME]
    name_col = mongo_db[COLLECTION_NAME]

    file_path = os.path.join(os.path.dirname(__file__), "data", "llm_name.json")
    with open(file_path, "r", encoding="utf-8") as f:
        authors = json.load(f)

    ops = []
    now = datetime.now(timezone.utc)
    total = len(authors)

    for i, item in enumerate(authors, start=1):
        ops.append(
            UpdateOne(
                {"name": item["name"]},
                {"$set": {"score": round(item["results"], 1)}},
                upsert=True,
            )
        )

        # 일정 개수씩 bulk_write 실행 → 메모리 과다 방지
        if len(ops) >= batch_size:
            name_col.bulk_write(ops, ordered=False)
            ops.clear()
            print(f"[{i}/{total}] bulk committed")

    # 남은 작업 처리
    if ops:
        name_col.bulk_write(ops, ordered=False)
        print(f"[{total}/{total}] final bulk committed")

def edit_json():
    file_path = os.path.join(os.path.dirname(__file__), "data", "llm_name.json")
    with open(file_path, "r", encoding="utf-8") as f:
        authors = json.load(f)

    new_names = []
    for item in authors:
        new_names.append({"name": item["name"], "score": item["results"]})

    with open(os.path.join(os.path.dirname(__file__), "data", "llm_name2.json"), "w", encoding="utf-8") as f:
        json.dump(new_names, f, ensure_ascii=False, indent=2)

    
if __name__ == "__main__":
    edit_json()
