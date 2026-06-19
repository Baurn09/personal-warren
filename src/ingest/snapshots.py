"""Raw snapshot storage — immutable audit copies of fetched data.

Every score must be reproducible from its source data, so the raw payload that
produced a stored value is written to ``data/raw/`` and recorded in the
``snapshots`` table.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_config, resolve


def save_snapshot(conn: sqlite3.Connection, content: str | bytes, *,
                  source: str, kind: str, ticker: str | None = None,
                  ext: str = "json") -> Path:
    """Write a raw payload to ``data/raw/<kind>/`` and record it in ``snapshots``.

    Returns the path of the written file.
    """
    cfg = load_config()
    raw_dir = resolve(cfg["paths"]["raw_snapshots"]) / kind
    raw_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = [kind, source]
    if ticker:
        parts.append(ticker.replace(".", "_"))
    parts.append(stamp)
    out = raw_dir / ("_".join(parts) + "." + ext)

    if isinstance(content, bytes):
        out.write_bytes(content)
    else:
        out.write_text(content, encoding="utf-8")

    conn.execute(
        """INSERT INTO snapshots (ticker, source, kind, path, fetched_at)
           VALUES (?,?,?,?,?)""",
        (ticker, source, kind, str(out), datetime.now(timezone.utc).isoformat()),
    )
    return out
