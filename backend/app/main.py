"""FastAPI entry point. Run with: uvicorn app.main:app --reload (from backend/)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.middleware.gzip import GZipMiddleware

from app.api import defenses, draft, errors, leagues, live_draft, meta, players, start_sit, tags, tiers, trades
from app.api.errors import error_response
from app.api.static import SiteFiles
from app.api.security import add_security_headers
from app.api.ratelimit import RateLimiter, Rule, retry_after_header
from app.config import settings
from app.data.refresh import Refresher

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"



@asynccontextmanager
async def lifespan(_app: FastAPI):
    # With AUTO_REFRESH_HOURS set, keep the synced and collected data fresh in the background.
    refresher = Refresher(settings.auto_refresh_hours) if settings.auto_refresh_hours > 0 else None
    if refresher:
        refresher.start()
    try:
        yield
    finally:
        if refresher:
            refresher.stop(timeout=1)


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
errors.install(app)

# Compress JSON and the static site (/players is a few hundred KB). Starlette leaves
# text/event-stream alone, so the live draft's events still stream as they happen.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Requests are small (a trade is a few dozen ids); anything bigger is a mistake or abuse.
MAX_BODY_BYTES = 256 * 1024


def _is_leagues(path: str) -> bool:
    return path == "/leagues" or path.startswith("/leagues/")


# Page loads fetch a handful of assets, so the general limit is generous. Endpoints that
# trigger requests to outside providers get their own, much smaller, bucket. Making and
# removing leagues writes to the server's storage, so it gets the strictest bucket; saving
# a league's roster happens on every edit, so it gets a roomier one.
limiter = RateLimiter(
    [
        Rule("all", burst=300, per_second=5, matches=lambda path: True),
        Rule("upstream", burst=10, per_second=0.2,
             matches=lambda path: path.startswith("/players/") and path.endswith("/profile")),
        Rule("league-changes", burst=settings.league_change_burst, per_second=settings.league_change_per_second,
             matches=_is_leagues, methods=frozenset({"POST", "DELETE"})),
        Rule("league-edits", burst=60, per_second=1, matches=_is_leagues, methods=frozenset({"PUT"})),
        # Connecting a live draft reads ESPN and holds the league's cookies in memory.
        Rule("live-draft-start", burst=6, per_second=0.05,
             matches=lambda path: path == "/draft/live", methods=frozenset({"POST"})),
    ],
    trust_proxy=settings.trust_proxy,
    proxy_hops=settings.trusted_proxy_hops,
)
limiter.enabled = settings.rate_limit_enabled


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    wait = limiter.check(limiter.client_key(request), request.url.path, request.method)
    if wait is not None:
        return error_response(
            429, "Too many requests. Slow down and try again shortly.",
            headers={"Retry-After": retry_after_header(wait)},
        )
    return await call_next(request)


class _BodyTooLarge(Exception):
    pass


class LimitBodySize:
    """Rejects request bodies over `max_bytes` with a 413.

    A Content-Length header over the limit is refused before anything is read. Bodies
    without one (Transfer-Encoding: chunked) are counted as they arrive, and the read is
    cut off as soon as the count passes the limit, so no route buffers more than that.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self):
        return error_response(413, f"Request body is larger than {self.max_bytes // 1024} KB.")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope["headers"]:
            if name == b"content-length":
                if value.isdigit() and int(value) > self.max_bytes:
                    return await self._too_large()(scope, receive, send)
                break

        received = 0
        rejected = False
        started = False

        async def counting_receive():
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message):
            nonlocal started
            # Once the body is refused, whatever the route makes of the aborted read (FastAPI
            # turns it into a 400) is dropped in favour of the 413.
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not rejected:
                raise
        if rejected and not started:
            await self._too_large()(scope, receive, send)


# Added here, before the security headers middleware below, so it runs inside it and its 413s
# get the headers too.
app.add_middleware(LimitBodySize, max_bytes=MAX_BODY_BYTES)


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
app.include_router(live_draft.router)
app.include_router(start_sit.router)
app.include_router(defenses.router)
app.include_router(tiers.router)
app.include_router(leagues.router)
app.include_router(tags.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve the trade analyzer UI at "/". Mounted last so API routes take priority.
if FRONTEND_DIR.is_dir():
    app.mount("/", SiteFiles(directory=FRONTEND_DIR, html=True), name="frontend")
