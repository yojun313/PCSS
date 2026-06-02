"""
gunicorn app.main:app -c run.py
uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn
import socket
import warnings
from requests.exceptions import RequestsDependencyWarning

warnings.filterwarnings("ignore", category=RequestsDependencyWarning)

if __name__ == "__main__":
    if socket.gethostname() == "knpu":
        port = 8004
    else:
        port = 8000

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        log_level="warning",
        access_log=False,
        timeout_keep_alive=86400,
    )
