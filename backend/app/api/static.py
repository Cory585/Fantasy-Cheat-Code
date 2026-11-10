"""Static file serving for the frontend, with a real 404 page for browsers.

Starlette serves `404.html` for any missing path when `html=True`. That's right for a
person following a bad link, but an API client hitting a mistyped endpoint should get the
JSON error, so the HTML page is only returned when the client asks for HTML.
"""

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.errors import ApiError


def wants_html(scope: Scope) -> bool:
    """True if the Accept header lists text/html (or */* from a browser-style list) with q > 0."""
    for name, value in scope.get("headers", []):
        if name != b"accept":
            continue
        for part in value.decode("latin-1").split(","):
            media, *params = [p.strip() for p in part.split(";")]
            q = 1.0
            for param in params:
                key, _, val = param.partition("=")
                if key.strip() == "q":
                    try:
                        q = float(val)
                    except ValueError:
                        q = 0.0
            if media.lower() == "text/html" and q > 0:
                return True
    return False


class SiteFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise ApiError(404, "not_found", "Nothing here. Check the URL; the API is documented at /docs.") from exc
            raise
        if response.status_code == 404 and not wants_html(scope):
            raise ApiError(404, "not_found", "Nothing here. Check the URL; the API is documented at /docs.")
        return response
