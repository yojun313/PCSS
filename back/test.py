import json
import os
from pymongo import MongoClient, UpdateOne
from datetime import datetime
from dotenv import load_dotenv
from tqdm import tqdm  # 진행 바 출력용

# .env 로드
load_dotenv()

# MongoDB 접속 정보
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "pcss"
COLLECTION_NAME = "llm_names"

# JSON 파일 경로
JSON_PATH = os.path.join(os.path.dirname(__file__), 'data', 'llm_name.json')

def migrate_json_to_mongo():
    # MongoDB 연결
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    # JSON 데이터 로드
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # bulk 작업 준비
    operations = []
    for name, score in tqdm(data.items(), desc="📤 Uploading to MongoDB", unit="item"):
        operations.append(
            UpdateOne(
                {"name": name},
                {
                    "$set": {
                        "score": score,
                        "updated_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
        )

    # bulk write 실행
    if operations:
        result = collection.bulk_write(operations)
        print(f"\n✅ 완료: {len(operations)}개 항목 처리됨 (삽입: {result.upserted_count}, 수정: {result.modified_count})")
    else:
        print("⚠️ 삽입할 데이터가 없습니다.")

if __name__ == "__main__":
    migrate_json_to_mongo()
