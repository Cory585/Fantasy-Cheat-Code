"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.app_name, version=settings.version)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
