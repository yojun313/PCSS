from fastapi import FastAPI
from app.routes.search_routes import router as search_router

app = FastAPI(
    title="PCS Search API",
    version="1.0.0"
)

app.include_router(search_router, prefix="/api")
