import os

LLM_SERVER = os.getenv('SERVER_IP', '')
PORT = os.getenv('SERVER_PORT', '')
LLM_URL = f"{LLM_SERVER}/v1/chat/completions",
LLM_MODEL = os.getenv('CUSTOM_MODEL', '/models/Qwen__Qwen3-VL-8B-Instruct-FP8')