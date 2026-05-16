"""Shared httpx client config — TLS plays nicely behind corporate proxies."""

from __future__ import annotations

import os
import ssl
from pathlib import Path

import httpx
import truststore

DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
DEFAULT_HEADERS = {
    "User-Agent": "fakethirtyeight/0.1 (+https://github.com/palewire/fakethirtyeight.com)",
    "Accept-Encoding": "gzip",
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


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        verify=make_ssl_context(),
    )
