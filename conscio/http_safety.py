"""HTTP safety utilities ensuring sensitive requests do not follow redirects."""
from __future__ import annotations

import urllib.request
from typing import Any

__all__ = ["open_no_redirect"]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses redirects to prevent leaking sensitive credentials."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def open_no_redirect(
    request: urllib.request.Request | str,
    timeout: float = 30.0,
) -> Any:
    """Open a URL or Request without following redirects to avoid re-sending sensitive credentials."""
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)
