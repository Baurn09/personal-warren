"""On-demand single-company ingest for the Company Analyzer.

Fetches one company's ``.info`` (profile + fundamentals) and ~6 years of prices,
reusing the Phase-1 extraction logic so the metrics, derivations and raw
snapshot stay identical to the universe pipeline. Works for any Indian listed
symbol, whether or not it is in the cached Nifty 500 universe.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from src.config import load_config, resolve
from src.db.schema import connect, init_db
from src.ingest import prices as prices_mod
from src.ingest.fundamentals import enrich_from_balance_sheet, extract_metrics
from src.ingest.snapshots import save_snapshot
from src.scoring import sector_models

# metric -> source preference (lower index wins when a metric has several sources)
_SOURCE_PREF = {"yfinance": 0, "yfinance-derived": 1, "yfinance-balancesheet": 2}


def _div_yield(info: dict):
    """Normalise yfinance dividendYield (sometimes a fraction, sometimes a %)."""
    dy = info.get("dividendYield")
    try:
        dy = float(dy)
    except (TypeError, ValueError):
        return None
    if dy != dy:                       # NaN
        return None
    return dy / 100.0 if dy > 1.0 else dy


def _ensure_company(conn, ticker: str, info: dict) -> None:
    name = info.get("longName") or info.get("shortName") or ticker
    conn.execute(
        """INSERT INTO companies (ticker, name, sector, industry, market_cap, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(ticker) DO UPDATE SET
               name=excluded.name,
               sector=COALESCE(excluded.sector, companies.sector),
               industry=COALESCE(excluded.industry, companies.industry),
               market_cap=COALESCE(excluded.market_cap, companies.market_cap),
               updated_at=excluded.updated_at""",
        (ticker, name, info.get("sector"), info.get("industry"),
         info.get("marketCap"), datetime.now(timezone.utc).isoformat()))


def _store_fundamentals(conn, ticker: str, tk, info: dict) -> dict:
    metrics = extract_metrics(info)
    if "total_debt" not in metrics or "roe" not in metrics:
        enrich_from_balance_sheet(tk, info, metrics)
    now = datetime.now(timezone.utc).isoformat()
    save_snapshot(conn, json.dumps(info, default=str, indent=2),
                  source="yfinance", kind="fundamentals", ticker=ticker)
    conn.executemany(
        """INSERT OR REPLACE INTO fundamentals
           (ticker, period, metric, value, source, fetched_at)
           VALUES (?,?,?,?,?,?)""",
        [(ticker, "TTM", m, v, src, now) for m, (v, src) in metrics.items()])
    return {m: v for m, (v, _src) in metrics.items()}


def _load_metrics(conn, ticker: str) -> dict:
    """Latest TTM metrics, picking the most authoritative source per metric."""
    rows = conn.execute(
        "SELECT metric, value, source FROM fundamentals "
        "WHERE ticker=? AND period='TTM'", (ticker,)).fetchall()
    best: dict[str, tuple[int, float]] = {}
    for metric, value, source in rows:
        pref = _SOURCE_PREF.get(source, 9)
        if metric not in best or pref < best[metric][0]:
            best[metric] = (pref, value)
    return {m: v for m, (_p, v) in best.items()}


def fetch_company_data(ticker: str, refresh: bool = False) -> dict:
    """Fetch (or load cached) profile, fundamentals and prices for one ticker.

    Returns a profile dict with ``metrics`` (TTM) and a ``prices`` DataFrame
    (ascending by date). Raises ``RuntimeError`` if the symbol can't be fetched.
    """
    cfg = load_config()
    suffix = cfg["ingest"]["yfinance_suffix"]
    db_path = resolve(cfg["paths"]["database"])
    init_db(db_path)

    with connect(db_path) as conn:
        have_fund = conn.execute(
            "SELECT 1 FROM fundamentals WHERE ticker=? LIMIT 1",
            (ticker,)).fetchone()
        have_px = conn.execute(
            "SELECT COUNT(*) FROM prices WHERE ticker=?", (ticker,)).fetchone()[0]

    info: dict = {}
    need_info = refresh or not have_fund
    if need_info:
        tk = yf.Ticker(ticker + suffix)
        try:
            info = tk.info or {}
        except Exception as e:                                  # noqa: BLE001
            raise RuntimeError(f"yfinance lookup failed for {ticker}: {e}")
        if not (info.get("shortName") or info.get("longName")):
            raise RuntimeError(f"no data found for symbol {ticker}{suffix}")
        with connect(db_path) as conn:
            _ensure_company(conn, ticker, info)
            _store_fundamentals(conn, ticker, tk, info)

    if refresh or have_px == 0:
        prices_mod.fetch_prices(tickers=[ticker])

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT name, sector, industry FROM companies WHERE ticker=?",
            (ticker,)).fetchone()
        metrics = _load_metrics(conn, ticker)
        px = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker=? ORDER BY date", conn, params=(ticker,))

    name = (row["name"] if row else None) or info.get("longName") \
        or info.get("shortName") or ticker
    sector = (row["sector"] if row else None) or info.get("sector")
    industry = (row["industry"] if row else None) or info.get("industry")
    template = sector_models.classify(industry, sector)

    # dividend yield: store into metrics so the estimator/dossier can use it
    dy = _div_yield(info)
    if dy is not None:
        metrics.setdefault("dividend_yield", dy)

    current_price = float(px["close"].iloc[-1]) if not px.empty else None

    return {
        "ticker": ticker,
        "symbol": ticker + suffix,
        "name": name,
        "sector": sector,
        "industry": industry,
        "template": template,
        "business_summary": info.get("longBusinessSummary"),
        "dividend_yield": dy,
        "metrics": metrics,
        "prices": px,
        "current_price": current_price,
        "history_days": len(px),
    }
