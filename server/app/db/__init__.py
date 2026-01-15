from pymongo import MongoClient
import os
from dotenv import load_dotenv 

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "pcss"
COLLECTION_NAME = "llm_names"

mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client[DB_NAME]
name_col = mongo_db[COLLECTION_NAME]
errors_col = mongo_db["errors"]