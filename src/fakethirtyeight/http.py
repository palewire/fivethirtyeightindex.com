"""Shared httpx client config — TLS plays nicely behind corporate proxies."""

from __future__ import annotations

import os
import ssl
from pathlib import Path

import httpx
import truststore

DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
DEFAULT_HEADERS = {
    "User-Agent": "fakethirtyeight/0.1 (+https://github.com/palewire/fivethirtyeightindex.com)",
    # Wayback's id_ endpoint sometimes returns brotli-encoded HTML regardless
    # of what we advertise, so we accept both. The brotli decoder is a project
    # dep (httpx uses it automatically when present).
    "Accept-Encoding": "gzip, br",
}


def make_ssl_context() -> ssl.SSLContext:
    """Build an SSL context for the crawler.

    Priority order:
      1. ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` env var pointing at a PEM
         bundle (most reliable for explicit corporate-CA setups).
      2. ``truststore`` against the OS keychain (works behind TLS-inspecting
         proxies on macOS/Windows/Linux).
    """
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle and Path(bundle).exists():
        return ssl.create_default_context(cafile=bundle)
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _ia_auth_header() -> dict[str, str]:
    """Internet Archive S3-style auth header, when keys are present.

    Generate keys at https://archive.org/account/s3.php and export them as
    ``IA_ACCESS_KEY`` and ``IA_SECRET_KEY``. Required for CDX prefix/domain
    queries against high-traffic news domains (e.g. ``*.nytimes.com``) that
    the public endpoint rejects with 403.
    """
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if access and secret:
        return {"Authorization": f"LOW {access}:{secret}"}
    return {}


def make_client() -> httpx.Client:
    headers = {**DEFAULT_HEADERS, **_ia_auth_header()}
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers=headers,
        follow_redirects=True,
        verify=make_ssl_context(),
    )
