"""Request helpers that depend on the deployment topology."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """The visitor's IP, for rate limiting.

    Behind our reverse proxy (Caddy) ``REMOTE_ADDR`` is the proxy itself, so with
    ``TRUSTED_PROXY_HOPS = n`` we take the n-th address from the *right* of
    ``X-Forwarded-For``: those entries were appended by proxies we run, while
    anything further left was supplied by the client and can be forged.
    """
    hops = getattr(settings, "TRUSTED_PROXY_HOPS", 0)
    if hops:
        forwarded = [
            part.strip()
            for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= hops:
            return forwarded[-hops]
    return request.META.get("REMOTE_ADDR", "unknown")
