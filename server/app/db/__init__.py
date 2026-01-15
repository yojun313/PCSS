from pymongo import MongoClient
import os
from dotenv import load_dotenv 

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "pcss"
COLLECTION_NAME = "llm_names"

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=20000,  # 서버 선택 최대 20초
    connectTimeoutMS=20000,          # TCP 연결 최대 20초
    socketTimeoutMS=60000,           # 쿼리 응답 대기 60초

    retryWrites=True,
    retryReads=True,
)
mongo_db = mongo_client[DB_NAME]
name_col = mongo_db[COLLECTION_NAME]
errors_col = mongo_db["errors"]