"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from pathlib import Path

from fastapi import FastAPI, Request

from app.api import defenses, draft, errors, meta, players, start_sit, trades
from app.api.errors import error_response
from app.api.static import SiteFiles
from app.api.security import add_security_headers
from app.api.ratelimit import RateLimiter, Rule, retry_after_header
from app.config import settings

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title=settings.app_name, version=settings.version)
errors.install(app)

# Requests are small (a trade is a few dozen ids); anything bigger is a mistake or abuse.
MAX_BODY_BYTES = 256 * 1024


# Page loads fetch a handful of assets, so the general limit is generous. Endpoints that
# trigger requests to outside providers get their own, much smaller, bucket.
limiter = RateLimiter(
    [
        Rule("all", burst=300, per_second=5, matches=lambda path: True),
        Rule("upstream", burst=10, per_second=0.2, matches=lambda path: path.endswith("/profile")),
    ],
    trust_proxy=settings.trust_proxy,
    proxy_hops=settings.trusted_proxy_hops,
)
limiter.enabled = settings.rate_limit_enabled


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    wait = limiter.check(limiter.client_key(request), request.url.path)
    if wait is not None:
        return error_response(
            429, "Too many requests. Slow down and try again shortly.",
            headers={"Retry-After": retry_after_header(wait)},
        )
    return await call_next(request)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return error_response(413, f"Request body is larger than {MAX_BODY_BYTES // 1024} KB.")
    return await call_next(request)


@app.exception_handler(RecursionError)
async def too_deeply_nested(request: Request, exc: RecursionError):
    # A safety net: deeply nested bodies are normally rejected as 422 (validation) or 400
    # (unparseable) before anything recurses this far, but nothing else should turn a stack
    # overflow into a 500.
    return error_response(400, "Request body is nested too deeply.", "body_too_deep")


# Registered last so it's the outermost middleware: 413s and 429s get the headers too. (500s are
# produced outside the middleware stack, so the error handler adds them itself.)
app.middleware("http")(add_security_headers)

app.include_router(players.router)
app.include_router(trades.router)
app.include_router(meta.router)
app.include_router(draft.router)
app.include_router(start_sit.router)
app.include_router(defenses.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the trade analyzer UI at "/". Mounted last so API routes take priority.
if FRONTEND_DIR.is_dir():
    app.mount("/", SiteFiles(directory=FRONTEND_DIR, html=True), name="frontend")
