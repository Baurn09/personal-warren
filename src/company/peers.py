"""Assemble a few sector peers on demand for a genuine sector-relative score.

Peers are drawn from the cached ``companies`` table (same sector, largest by
market cap). Any peer missing fundamentals is fetched on demand. If too few
peers can be assembled the caller falls back to absolute quality scoring.
"""
from __future__ import annotations

import pandas as pd

from src.config import load_config, resolve
from src.db.schema import connect
from src.ingest import fundamentals as fund_mod
from src.scoring import sector_models

# the metric columns the scoring engine reads (superset of both templates)
_METRIC_COLS = [
    "roe", "roa", "net_margin", "operating_margin", "gross_margin",
    "revenue_growth", "earnings_growth", "current_ratio", "cash_conversion",
    "promoter_holding", "fcf", "pe", "price_to_book", "ev_ebitda",
    "debt_to_equity",
]
_SOURCE_PREF = {"yfinance": 0, "yfinance-derived": 1, "yfinance-balancesheet": 2}


def _select_peers(conn, ticker: str, sector: str | None, n: int) -> list[str]:
    if not sector:
        return []
    rows = conn.execute(
        "SELECT ticker FROM companies WHERE sector=? AND ticker!=? "
        "ORDER BY market_cap DESC NULLS LAST LIMIT ?",
        (sector, ticker, n)).fetchall()
    return [r[0] for r in rows]


def _metrics_frame(conn, tickers: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, metric, value, source FROM fundamentals "
        f"WHERE period='TTM' AND ticker IN ({placeholders})", tickers).fetchall()
    best: dict[str, dict[str, tuple[int, float]]] = {}
    for ticker, metric, value, source in rows:
        pref = _SOURCE_PREF.get(source, 9)
        slot = best.setdefault(ticker, {})
        if metric not in slot or pref < slot[metric][0]:
            slot[metric] = (pref, value)
    return {t: {m: v for m, (_p, v) in d.items()} for t, d in best.items()}


def build_peer_frame(ticker: str, sector: str | None, template: str,
                     target_metrics: dict) -> tuple[pd.DataFrame, str, list[str]]:
    """Build the ``score()`` input frame for ``{target + peers}``.

    Returns ``(frame, mode, peer_tickers)`` where ``mode`` is ``relative`` (enough
    peers) or ``absolute`` (fall back to threshold scoring). The frame is indexed
    by ticker with ``sector``, ``template`` and metric columns.
    """
    cfg = load_config()
    ca = cfg["company_analysis"]["peers"]
    db_path = resolve(cfg["paths"]["database"])

    with connect(db_path) as conn:
        peer_tickers = _select_peers(conn, ticker, sector, int(ca["sample_size"]))
        missing = []
        if peer_tickers:
            present = {r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM fundamentals WHERE ticker IN "
                f"({','.join('?' * len(peer_tickers))})", peer_tickers)}
            missing = [t for t in peer_tickers if t not in present]

    if missing:
        fund_mod.fetch_fundamentals(tickers=missing)

    with connect(db_path) as conn:
        peer_metrics = _metrics_frame(conn, peer_tickers) if peer_tickers else {}

    rows: dict[str, dict] = {ticker: dict(target_metrics)}
    rows.update(peer_metrics)

    records = []
    index = []
    for t, m in rows.items():
        tpl = template if t == ticker else sector_models.classify(None, sector)
        rec = {"sector": sector, "template": tpl}
        for col in _METRIC_COLS:
            rec[col] = m.get(col)
        records.append(rec)
        index.append(t)
    frame = pd.DataFrame(records, index=index)

    n_peers = len(peer_metrics)
    mode = "relative" if n_peers >= int(ca["min_for_relative"]) else "absolute"
    return frame, mode, list(peer_metrics.keys())


def sector_median_pe(frame: pd.DataFrame, target_ticker: str) -> float | None:
    """Median PE of the peers (excluding the target), for the re-rating anchor."""
    if "pe" not in frame.columns:
        return None
    peers = frame.drop(index=target_ticker, errors="ignore")["pe"].dropna()
    peers = peers[peers > 0]
    return float(peers.median()) if not peers.empty else None
