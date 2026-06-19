"""Company-specific news — Google News RSS, falling back to yfinance.

The market-wide RSS feed in ``src.ingest.news`` is not tagged per company, so
the analyzer fetches headlines for the specific company by name. Best-effort:
network failures degrade silently to an empty list. Headlines are snapshotted
and written to the shared ``news`` table tagged with the ticker.
"""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from src.config import load_config, resolve
from src.db.schema import connect
from src.ingest.http import make_session
from src.ingest.snapshots import save_snapshot

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def _from_google(name: str, limit: int) -> tuple[list[dict], bytes | None]:
    session = make_session()
    query = f'"{name}" stock OR shares OR results when:30d'
    params = {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    resp = session.get(GOOGLE_NEWS_RSS, params=params, timeout=20)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries[:limit]:
        src = ""
        if getattr(e, "source", None) is not None:
            src = getattr(e.source, "title", "") or ""
        items.append({
            "title": getattr(e, "title", "").strip(),
            "url": getattr(e, "link", ""),
            "published": getattr(e, "published", ""),
            "source": src or "Google News",
        })
    return items, resp.content


def _from_yfinance(ticker: str, suffix: str, limit: int) -> list[dict]:
    import yfinance as yf
    raw = yf.Ticker(ticker + suffix).news or []
    items = []
    for n in raw[:limit]:
        ts = n.get("providerPublishTime")
        published = ""
        if ts:
            published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        items.append({
            "title": n.get("title", "").strip(),
            "url": n.get("link", ""),
            "published": published,
            "source": n.get("publisher", "yfinance"),
        })
    return items


def fetch_company_news(name: str, ticker: str, limit: int = 8) -> list[dict]:
    """Fetch recent company-specific headlines (best-effort). Persists to ``news``."""
    cfg = load_config()
    suffix = cfg["ingest"]["yfinance_suffix"]
    db_path = resolve(cfg["paths"]["database"])

    items: list[dict] = []
    raw_xml: bytes | None = None
    try:
        items, raw_xml = _from_google(name, limit)
    except Exception:                                          # noqa: BLE001
        items = []
    if not items:
        try:
            items = _from_yfinance(ticker, suffix, limit)
        except Exception:                                      # noqa: BLE001
            items = []

    now = datetime.now(timezone.utc).isoformat()
    try:
        with connect(db_path) as conn:
            if raw_xml:
                save_snapshot(conn, raw_xml, source="google_news",
                              kind="company_news", ticker=ticker, ext="xml")
            for it in items:
                if not it.get("url"):
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO news "
                    "(ticker, source, title, url, published, fetched_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (ticker, it["source"], it["title"], it["url"],
                     it["published"], now))
    except Exception:                                          # noqa: BLE001
        pass
    return items
