from app.db import errors_col
from datetime import datetime
from bson import ObjectId
from fastapi import Request


def write_log(run_id, message):
    try:
        # 문서가 없으면 새로 생성
        if run_id is None:
            run_id = ObjectId()
            errors_col.insert_one(
                {
                    "_id": run_id,
                    "started_at": datetime.utcnow(),
                    "errors": [{"timestamp": datetime.utcnow(), "message": message}],
                }
            )
        else:
            errors_col.update_one(
                {"_id": run_id},
                {
                    "$push": {
                        "errors": {"timestamp": datetime.utcnow(), "message": message}
                    }
                },
            )
    except Exception as e:
        print("Failed to write log:", str(e))


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # "client, proxy1, proxy2" 형태일 수 있음
        return xff.split(",")[0].strip()
    xrip = request.headers.get("x-real-ip")
    if xrip:
        return xrip.strip()
    if request.client:
        return request.client.host
    return "unknown"
