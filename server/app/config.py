import os

LLM_SERVER = os.getenv('SERVER_IP', '')
PORT = os.getenv('SERVER_PORT', '')
LLM_URL = os.getenv('CUSTOM_API_URL', '')
LLM_MODEL = os.getenv('CUSTOM_MODEL', '/models/Qwen__Qwen3-VL-8B-Instruct-FP8')
FORCE_CRAWL = os.getenv('FORCE_CRAWL', 'false').lower() == 'true'