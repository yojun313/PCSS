import os

LLM_SERVER = os.getenv("SERVER_IP", "")
PORT = os.getenv("SERVER_PORT", "")
LLM_URL = os.getenv("CUSTOM_API_URL", "")
NAME_CACHE = os.getenv("NAME_CACHE", "true").lower() == "true"
