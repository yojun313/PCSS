import os

LLM_SERVER = os.getenv('SERVER_IP', '')
PORT = os.getenv('PORT', '8089')
API_URL = f"http://{LLM_SERVER}:{PORT}/api/process"
LLM_MODEL = 'llama3.3:70b-instruct-q8_0'