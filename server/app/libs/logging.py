from app.db import errors_col
from datetime import datetime
from bson import ObjectId


def write_log(run_id, message):
    try:
        # 문서가 없으면 새로 생성
        if run_id is None:
            run_id = ObjectId()
            errors_col.insert_one({
                "_id": run_id,
                "started_at": datetime.utcnow(),
                "errors": [{
                    "timestamp": datetime.utcnow(),
                    "message": message
                }]
            })
        else:
            errors_col.update_one(
                {"_id": run_id},
                {"$push": {
                    "errors": {
                        "timestamp": datetime.utcnow(),
                        "message": message
                    }
                }}
            )
    except Exception as e:
        print("Failed to write log:", str(e))