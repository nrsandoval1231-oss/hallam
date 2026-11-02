"""Optional bearer-token authentication for the dashboard and its API.

The V1 product is a single-operator tool: by default it binds to 127.0.0.1 and no
token is required. The moment it is exposed beyond localhost, `SABRA_API_TOKEN`
must be set, and this middleware then gates every route except the health check
and brand assets.

Three credential presentations are accepted, checked in order:

* `Authorization: Bearer <token>` (the API and scripted clients).
* A one-time `?token=<token>` on a GET: validated, then exchanged for an
  HttpOnly cookie and redirected so the secret never lingers in the URL.
* The `artemis_token` cookie itself, set by the exchange above.

The cookie is `SameSite=Strict`, so cross-site requests never carry it; mutating
endpoints accept JSON bodies only, which a cross-site form cannot send without a
CORS preflight this server does not answer. Comparison is constant-time.
"""

from __future__ import annotations

import hmac
from urllib.parse import urlencode

from sabra_domain.settings import get_settings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp

COOKIE_NAME = "artemis_token"
# Paths that must stay reachable without credentials: health probes and brand
# assets (fonts, logos) that the login-free favicon and error pages reference.
PUBLIC_PATHS = ("/health", "/favicon.svg")
PUBLIC_PREFIXES = ("/brand/",)

_UNAUTHORIZED_API = JSONResponse(
    status_code=401,
    content={
        "detail": (
            "Authentication required. Send Authorization: Bearer <token> "
            "(SABRA_API_TOKEN)."
        )
    },
    headers={"WWW-Authenticate": "Bearer"},
)


def _unauthorized_page() -> Response:
    return Response(
        content=(
            "<!doctype html><html><head><title>Artemis — sign in</title></head>"
            "<body style=\"font-family:sans-serif;max-width:32em;margin:4em auto;"
            "color:#142D29\"><h1>Artemis</h1><p>This Artemis deployment requires a "
            "token. Open it with "
            "<code>?token=…</code> once (for example from your password manager) "
            "or send <code>Authorization: Bearer …</code>.</p></body></html>"
        ),
        status_code=401,
        media_type="text/html",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _token_ok(presented: str | None, expected: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented.encode(), expected.encode())


def _presented_token(request: Request) -> tuple[str | None, bool]:
    """The credential on this request, and whether it came from the URL.

    URL credentials are single-use: a valid one sets the cookie and redirects.
    """

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), False
    query_token = request.query_params.get("token")
    if query_token:
        return query_token, True
    return request.cookies.get(COOKIE_NAME), False


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Enforce `SABRA_API_TOKEN` when one is configured; otherwise pass through."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._warned_missing_token = False

    async def dispatch(self, request: Request, call_next: object) -> Response:
        settings = get_settings()
        expected = settings.api_token
        if not expected:
            # Local single-user mode: the API binds to 127.0.0.1 by default.
            return await call_next(request)  # type: ignore[operator]

        path = request.url.path
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)  # type: ignore[operator]

        presented, from_url = _presented_token(request)
        if _token_ok(presented, expected):
            if from_url:
                # Exchange the URL token for a cookie, then drop it from the address bar.
                target = request.url.path
                if request.url.query and request.url.query != f"token={presented}":
                    rest = {
                        key: value
                        for key, value in request.query_params.multi_items()
                        if key != "token"
                    }
                    if rest:
                        target += "?" + urlencode(rest)
                response: Response = RedirectResponse(url=target, status_code=303)
            else:
                response = await call_next(request)  # type: ignore[operator]
            if COOKIE_NAME not in request.cookies or not _token_ok(
                request.cookies.get(COOKIE_NAME), expected
            ):
                response.set_cookie(
                    COOKIE_NAME,
                    expected,
                    httponly=True,
                    samesite="strict",
                    secure=request.url.scheme == "https",
                    max_age=30 * 24 * 3600,
                    path="/",
                )
            return response

        return _UNAUTHORIZED_API if path.startswith("/api/") else _unauthorized_page()
