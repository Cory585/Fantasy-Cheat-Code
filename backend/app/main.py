"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from fastapi import FastAPI

from app.api import players, trades
from app.config import settings

app = FastAPI(title=settings.app_name, version=settings.version)
app.include_router(players.router)
app.include_router(trades.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
