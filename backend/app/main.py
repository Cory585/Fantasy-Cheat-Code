"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api import draft, errors, meta, players, trades
from app.api.errors import error_response
from app.config import settings

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title=settings.app_name, version=settings.version)
errors.install(app)

# Requests are small (a trade is a few dozen ids); anything bigger is a mistake or abuse.
MAX_BODY_BYTES = 256 * 1024


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return error_response(413, f"Request body is larger than {MAX_BODY_BYTES // 1024} KB.")
    return await call_next(request)


@app.exception_handler(RecursionError)
async def too_deeply_nested(request: Request, exc: RecursionError):
    # Python's JSON parser recurses per nesting level, so e.g. 5,000 nested arrays overflow it.
    return error_response(400, "Request body is nested too deeply.", "body_too_deep")


app.include_router(players.router)
app.include_router(trades.router)
app.include_router(meta.router)
app.include_router(draft.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the trade analyzer UI at "/". Mounted last so API routes take priority.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
