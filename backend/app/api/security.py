"""Security headers on every response.

The Content Security Policy only lets pages run scripts served by this app (no inline
scripts, no third-party scripts), and only load images from here and ESPN's image CDN,
so injected markup can't run code or pull from elsewhere. Styles allow inline `style`
attributes, which the trade analyzer uses for its field positions.

FastAPI's /docs and /redoc pages load Swagger/ReDoc from a CDN with inline scripts, so
they get every header except the CSP.
"""

from starlette.requests import Request

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https://a.espncdn.com",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    if not request.url.path.startswith(DOCS_PATHS):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response
