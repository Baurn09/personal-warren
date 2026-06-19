"""Ingest the Nifty 500 constituent list from NSE."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_config, resolve
from src.db.schema import connect
from src.ingest.http import nse_session
from src.ingest.snapshots import save_snapshot


def _load_csv_text(local_file: str | None) -> str:
    """Return the constituents CSV text, from a local file or from NSE."""
    cfg = load_config()
    if local_file:
        return Path(local_file).read_text(encoding="utf-8")
    url = cfg["universe"]["constituents_url"]
    timeout = cfg["ingest"]["request_timeout_seconds"]
    resp = nse_session().get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_universe(local_file: str | None = None) -> int:
    """Download (or load) Nifty 500 constituents and upsert into ``companies``.

    If NSE blocks the download, fetch the CSV manually from nseindia.com
    (Indices -> NIFTY 500) and pass its path as ``local_file``.

    Returns the number of companies written.
    """
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    text = _load_csv_text(local_file)
    now = datetime.now(timezone.utc).isoformat()
    rows = list(csv.DictReader(io.StringIO(text)))

    written = 0
    with connect(db_path) as conn:
        save_snapshot(conn, text, source="nse", kind="universe", ext="csv")
        for r in rows:
            symbol = (r.get("Symbol") or "").strip()
            # NSE seeds the index list with DUMMY* placeholders during corporate
            # restructurings (e.g. demergers) — these are not investable stocks.
            if not symbol or symbol.upper().startswith("DUMMY"):
                continue
            conn.execute(
                """INSERT INTO companies (ticker, name, industry, isin, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     name=excluded.name,
                     industry=excluded.industry,
                     isin=excluded.isin,
                     updated_at=excluded.updated_at""",
                (symbol,
                 (r.get("Company Name") or "").strip(),
                 (r.get("Industry") or "").strip(),
                 (r.get("ISIN Code") or "").strip(),
                 now),
            )
            written += 1
    return written
