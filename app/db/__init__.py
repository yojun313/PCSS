from pymongo import MongoClient
import os
from dotenv import load_dotenv
import socket
import platform

load_dotenv()

DB_NAME = "pcss"

SSH_HOST = os.getenv("SSH_HOST")
SSH_PORT = int(os.getenv("SSH_PORT", 22))
SSH_USER = os.getenv("SSH_USER")
SSH_KEY = os.getenv("SSH_KEY")

# MongoDB 설정
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", 27017))
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_AUTH_DB = os.getenv("MONGO_AUTH_DB", "admin")

SSH_MONGO = os.getenv("SSH_MONGO", "false").lower() == "true"

if not SSH_MONGO:
    # 서버 내부에서 실행 → 로컬 MongoDB 바로 사용
    client = MongoClient(
        f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}"
        f"@localhost:{MONGO_PORT}/?authSource={MONGO_AUTH_DB}"
    )
else:
    import warnings

    warnings.filterwarnings("ignore", module="paramiko")
    from sshtunnel import SSHTunnelForwarder

    # 외부에서 실행 → SSH 터널 사용
    server = SSHTunnelForwarder(
        (SSH_HOST, SSH_PORT),
        ssh_username=SSH_USER,
        ssh_pkey=SSH_KEY,
        remote_bind_address=(MONGO_HOST, MONGO_PORT),
    )
    server.start()

    client = MongoClient(
        f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}"
        f"@127.0.0.1:{server.local_bind_port}/?authSource={MONGO_AUTH_DB}"
    )

mongo_db = client[DB_NAME]
name_col = mongo_db["llm_names"]
new_name_col = mongo_db["new_llm_names"]
errors_col = mongo_db["errors"]
conf_col = mongo_db["conferences"]
papers_col = mongo_db["papers"]
dblp_col = mongo_db["dblp"]
log_col = mongo_db["logs"]
req_korean_col = mongo_db["req_korean"]
subscription_col = mongo_db["subscription"]
auth_col = mongo_db["auth"]
admin_col = mongo_db["admin"]
users_col = mongo_db["users"]
pending_signups_col = mongo_db["pending_signups"]
password_resets_col = mongo_db["password_resets"]
board_notice_col = mongo_db["board_notice"]
board_bug_col = mongo_db["board_bug"]
authors_col = mongo_db["authors"]
blocked_ip_col = mongo_db["blocked_ips"]
notifier_state_col = mongo_db["notifier_state"]
scheduler_state_col = mongo_db["scheduler_state"]
