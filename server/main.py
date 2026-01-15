from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.core.paths import STATIC_DIR, TEMPLATES_DIR
from app.routes.search_routes import router as search_router
from app.routes.page_routes import router as page_router

app = FastAPI(title="PCSS API", version="1.0.0")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://pcss.r-e.kr",
        "https://pcss.r-e.kr",
        "http://pcss.r-e.kr:3000",
        "http://pcss.r-e.kr:8000",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router, prefix="/api")
app.include_router(page_router)
