"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import meta, players, trades
from app.config import settings

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title=settings.app_name, version=settings.version)

# Requests are small (a trade is a few dozen ids); anything bigger is a mistake or abuse.
MAX_BODY_BYTES = 256 * 1024


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": f"Request body is larger than {MAX_BODY_BYTES // 1024} KB."}, status_code=413)
    return await call_next(request)


@app.exception_handler(RecursionError)
async def too_deeply_nested(request: Request, exc: RecursionError) -> JSONResponse:
    # Python's JSON parser recurses per nesting level, so e.g. 5,000 nested arrays overflow it.
    return JSONResponse({"detail": "Request body is nested too deeply."}, status_code=400)


app.include_router(players.router)
app.include_router(trades.router)
app.include_router(meta.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the trade analyzer UI at "/". Mounted last so API routes take priority.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
