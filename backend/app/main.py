"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import meta, players, trades
from app.config import settings

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title=settings.app_name, version=settings.version)
app.include_router(players.router)
app.include_router(trades.router)
app.include_router(meta.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the trade analyzer UI at "/". Mounted last so API routes take priority.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
