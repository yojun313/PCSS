import os
import asyncio
import platform
import urllib.parse
from typing import Optional
from dotenv import load_dotenv
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
    AsyncIOMotorCollection,
)

load_dotenv()

# 기본 설정
DB_NAME = os.getenv("DB_NAME", "pcss")
SSH_MONGO = os.getenv("SSH_MONGO", "false").lower() == "true"

# SSH 설정
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

# 컬렉션 이름 매핑
COLLECTION_NAMES = {
    "llm_names": os.getenv("LLM_NAMES_COLLECTION", "llm_names"),
    "errors": os.getenv("ERRORS_COLLECTION", "errors"),
    "conferences": os.getenv("CONFERENCES_COLLECTION", "conferences"),
    "papers": os.getenv("PAPERS_COLLECTION", "papers"),
    "logs": "logs",
}

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None
_tunnel = None
_tunnel_local_port: Optional[int] = None


def _build_mongo_uri(host: str, port: int) -> str:
    user = urllib.parse.quote_plus(MONGO_USER) if MONGO_USER else ""
    password = urllib.parse.quote_plus(MONGO_PASSWORD) if MONGO_PASSWORD else ""

    if user and password:
        return f"mongodb://{user}:{password}@{host}:{port}/?authSource={MONGO_AUTH_DB}"
    return f"mongodb://{host}:{port}/?authSource={MONGO_AUTH_DB}"


async def _start_ssh_tunnel():
    """SSH 터널을 별도 스레드에서 실행"""
    from sshtunnel import SSHTunnelForwarder
    import warnings

    warnings.filterwarnings("ignore", module="paramiko")

    if not (SSH_HOST and SSH_USER and SSH_KEY):
        raise RuntimeError(
            "SSH_MONGO is true but SSH configuration (HOST/USER/KEY) is missing."
        )

    def _open_tunnel():
        tunnel = SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USER,
            ssh_pkey=SSH_KEY,
            remote_bind_address=(MONGO_HOST, MONGO_PORT),
        )
        tunnel.start()
        return tunnel

    return await asyncio.to_thread(_open_tunnel)


async def get_mongo_client() -> AsyncIOMotorClient:
    global _client, _tunnel, _tunnel_local_port

    if _client is not None:
        return _client

    if SSH_MONGO:
        if _tunnel is None:
            _tunnel = await _start_ssh_tunnel()
            _tunnel_local_port = int(_tunnel.local_bind_port)

        uri = _build_mongo_uri("127.0.0.1", _tunnel_local_port)
    else:
        uri = _build_mongo_uri(MONGO_HOST, MONGO_PORT)

    _client = AsyncIOMotorClient(uri)
    return _client


async def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is not None:
        return _db
    client = await get_mongo_client()
    _db = client[DB_NAME]
    return _db


async def get_collection(name: str) -> AsyncIOMotorCollection:
    db = await get_db()
    return db[name]


async def get_name_col():
    return await get_collection(COLLECTION_NAMES["llm_names"])


async def get_errors_col():
    return await get_collection(COLLECTION_NAMES["errors"])


async def get_conf_col():
    return await get_collection(COLLECTION_NAMES["conferences"])


async def get_papers_col():
    return await get_collection(COLLECTION_NAMES["papers"])


async def get_log_col():
    return await get_collection(COLLECTION_NAMES["logs"])
