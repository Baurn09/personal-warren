"""Ingest daily price history via yfinance.

The ``prices`` table (with its ``source`` column) is the price record; raw
per-ticker snapshots are not stored because prices are cheaply reproducible and
not prone to the silent corruption that fundamentals are.
"""
from __future__ import annotations

import math
import time

import yfinance as yf

from src.config import load_config, resolve
from src.db.schema import connect


def fetch_prices(limit: int | None = None,
                 tickers: list[str] | None = None) -> dict:
    """Fetch price history for companies and store it in ``prices``.

    By default every company in the universe is processed; pass ``tickers`` to
    refresh a specific subset (e.g. the single-company analyzer fetching one
    ticker plus a few sector peers).

    Returns a summary dict: ``{ok, failed, rows}``.
    """
    cfg = load_config()
    suffix = cfg["ingest"]["yfinance_suffix"]
    delay = cfg["ingest"]["request_delay_seconds"]
    period = f"{cfg['ingest']['price_history_years']}y"
    db_path = resolve(cfg["paths"]["database"])

    result = {"ok": 0, "failed": 0, "rows": 0}
    with connect(db_path) as conn:
        if tickers is None:
            tickers = [r["ticker"]
                       for r in conn.execute("SELECT ticker FROM companies")]
            if limit:
                tickers = tickers[:limit]

        for ticker in tickers:
            try:
                hist = yf.Ticker(ticker + suffix).history(
                    period=period, auto_adjust=False)
            except Exception:
                result["failed"] += 1
                continue
            if hist is None or hist.empty:
                result["failed"] += 1
                continue

            rows = []
            for idx, r in hist.iterrows():
                o, h, lo, c = r["Open"], r["High"], r["Low"], r["Close"]
                if any(math.isnan(x) for x in (o, h, lo, c)):
                    continue
                vol = r["Volume"]
                vol = 0 if (vol is None or math.isnan(vol)) else int(vol)
                rows.append((ticker, idx.strftime("%Y-%m-%d"),
                             float(o), float(h), float(lo), float(c),
                             vol, "yfinance"))

            conn.executemany(
                """INSERT OR REPLACE INTO prices
                   (ticker, date, open, high, low, close, volume, source)
                   VALUES (?,?,?,?,?,?,?,?)""", rows)
            result["ok"] += 1
            result["rows"] += len(rows)
            time.sleep(delay)
    return result
