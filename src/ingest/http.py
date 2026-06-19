"""Shared HTTP session helpers.

NSE rejects bare requests, so we present browser-like headers and (for NSE)
prime the session with cookies by visiting the homepage first.
"""
from __future__ import annotations

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def make_session() -> requests.Session:
    """A plain session with browser-like headers."""
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def nse_session() -> requests.Session:
    """A session primed with NSE cookies (NSE blocks un-cookied requests)."""
    s = make_session()
    try:
        s.get("https://www.nseindia.com", timeout=20)
    except requests.RequestException:
        pass  # the actual request will surface a clearer error
    return s
