"""Ingest market news from RSS feeds.

Phase 1 stores feed items market-wide (``ticker`` left NULL). Per-ticker tagging
is added in a later phase alongside embeddings.
"""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from src.config import load_config, resolve
from src.db.schema import connect
from src.ingest.http import make_session
from src.ingest.snapshots import save_snapshot


def fetch_news() -> int:
    """Fetch all configured RSS feeds into ``news``. Returns items inserted."""
    cfg = load_config()
    feeds = cfg["news"]["feeds"]
    timeout = cfg["ingest"]["request_timeout_seconds"]
    db_path = resolve(cfg["paths"]["database"])
    now = datetime.now(timezone.utc).isoformat()
    session = make_session()

    inserted = 0
    with connect(db_path) as conn:
        for source, url in feeds.items():
            try:
                raw = session.get(url, timeout=timeout).text
            except Exception:
                continue
            save_snapshot(conn, raw, source=source, kind="news", ext="xml")

            for entry in feedparser.parse(raw).entries:
                title = (getattr(entry, "title", "") or "").strip()
                link = (getattr(entry, "link", "") or "").strip()
                if not link:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO news
                       (ticker, source, title, url, published, fetched_at)
                       VALUES (?,?,?,?,?,?)""",
                    (None, source, title, link,
                     getattr(entry, "published", None), now))
                inserted += cur.rowcount
    return inserted
