"""Static file serving for the frontend, with a real 404 page for browsers.

Starlette serves `404.html` for any missing path when `html=True`. That's right for a
person following a bad link, but an API client hitting a mistyped endpoint should get the
JSON error, so the HTML page is only returned when the client asks for HTML.
"""

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.errors import ApiError


def wants_html(scope: Scope) -> bool:
    """True if the Accept header lists text/html with q > 0."""
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


NOT_FOUND = "Nothing here. Check the URL; the API is documented at /docs."


class SiteFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._not_found(scope)
            raise
        except (OSError, ValueError):
            # A path the OS can't even look up (e.g. control characters on Windows, or an
            # embedded NUL) is just a page that doesn't exist.
            return self._not_found(scope)
        if response.status_code == 404 and not wants_html(scope):
            raise ApiError(404, "not_found", NOT_FOUND)
        return response

    def _not_found(self, scope: Scope):
        page = Path(self.directory) / "404.html"
        if wants_html(scope) and page.is_file():
            return FileResponse(page, status_code=404)
        raise ApiError(404, "not_found", NOT_FOUND)
