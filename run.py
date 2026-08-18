"""
gunicorn app.main:app -c run.py
uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import os
from dotenv import load_dotenv
import uvicorn
import socket
import warnings
from requests.exceptions import RequestsDependencyWarning

warnings.filterwarnings("ignore", category=RequestsDependencyWarning)

load_dotenv()

PORT = os.getenv("PORT", 8000)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=PORT,
        workers=1,
        log_level="warning",
        access_log=False,
        timeout_keep_alive=86400,
    )
