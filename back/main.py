from fastapi import FastAPI
from app.routes.search_routes import router as search_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="PCSS API",
    version="1.0.0"
)

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