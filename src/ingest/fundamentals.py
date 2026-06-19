"""Ingest fundamentals via yfinance — the free Phase 1 source.

yfinance's ``.info`` dict is convenient but patchy. Two safeguards recover the
metrics it drops, both deterministic (no estimation):

* :func:`extract_metrics` derives ROE, PE and total debt from ``.info``
  primitives — tagged source ``yfinance-derived``.
* :func:`enrich_from_balance_sheet` falls back to the ``.balance_sheet``
  financial statement (more complete than ``.info``) — tagged source
  ``yfinance-balancesheet``. Only called when a metric is still missing.

Whatever is still absent is left for the data-quality gate
(:mod:`src.ingest.reconcile`) to flag.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import yfinance as yf

from src.config import load_config, resolve
from src.db.schema import connect
from src.ingest.snapshots import save_snapshot

# our metric name -> yfinance ``.info`` key (values taken as-is when present)
DIRECT_MAP = {
    # core (also checked by the data-quality gate)
    "revenue": "totalRevenue",
    "net_profit": "netIncomeToCommon",
    "total_debt": "totalDebt",
    "roe": "returnOnEquity",
    "pe": "trailingPE",
    "fcf": "freeCashflow",
    "debt_to_equity": "debtToEquity",
    "revenue_growth": "revenueGrowth",
    "market_cap": "marketCap",
    # scoring inputs (Phase 2) — all from the already-fetched .info payload
    "roa": "returnOnAssets",
    "net_margin": "profitMargins",
    "operating_margin": "operatingMargins",
    "gross_margin": "grossMargins",
    "price_to_book": "priceToBook",
    "ev_ebitda": "enterpriseToEbitda",
    "peg": "pegRatio",
    "earnings_growth": "earningsGrowth",
    "payout_ratio": "payoutRatio",
    "promoter_holding": "heldPercentInsiders",
    "current_ratio": "currentRatio",
    "operating_cashflow": "operatingCashflow",
    "book_value": "bookValue",
    "eps": "trailingEps",
}


def _num(value) -> float | None:
    """Coerce to a finite float, or return None (also drops NaN)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # f != f is True only for NaN


def extract_metrics(info: dict) -> dict[str, tuple[float, str]]:
    """Return ``{metric: (value, source)}`` from a yfinance ``.info`` payload.

    Direct keys are taken as-is. When ROE, PE or total debt is absent, it is
    derived from primitives yfinance does provide — a deterministic calculation.
    Derived values carry the source ``yfinance-derived`` so they stay auditable.
    """
    out: dict[str, tuple[float, str]] = {}
    for metric, key in DIRECT_MAP.items():
        val = _num(info.get(key))
        if val is not None:
            out[metric] = (val, "yfinance")

    # --- derive PE if absent (loss-makers legitimately have none) ---
    if "pe" not in out:
        price = _num(info.get("currentPrice")) or _num(info.get("previousClose"))
        eps = _num(info.get("trailingEps"))
        net = _num(info.get("netIncomeToCommon"))
        mcap = _num(info.get("marketCap"))
        if price is not None and eps is not None and eps > 0:
            out["pe"] = (price / eps, "yfinance-derived")
        elif mcap is not None and net is not None and net > 0:
            out["pe"] = (mcap / net, "yfinance-derived")

    # --- derive ROE if absent: net income / shareholders' equity ---
    if "roe" not in out:
        net = _num(info.get("netIncomeToCommon"))
        bvps = _num(info.get("bookValue"))            # book value per share
        shares = _num(info.get("sharesOutstanding"))
        if net is not None and bvps and shares:
            equity = bvps * shares
            if equity > 0:
                out["roe"] = (net / equity, "yfinance-derived")

    # --- derive total_debt if absent: (debt/equity %) x shareholders' equity ---
    # yfinance often omits totalDebt for near-debt-free companies; debtToEquity
    # is reported as a percentage (e.g. 161.977 -> a 1.62x debt/equity ratio).
    if "total_debt" not in out:
        dte = _num(info.get("debtToEquity"))
        bvps = _num(info.get("bookValue"))
        shares = _num(info.get("sharesOutstanding"))
        if dte is not None and bvps and shares:
            equity = bvps * shares
            if equity > 0:
                out["total_debt"] = (dte / 100.0 * equity, "yfinance-derived")

    # --- derive cash conversion: operating cash flow / net profit ---
    if "cash_conversion" not in out and "operating_cashflow" in out \
            and "net_profit" in out:
        ocf, net = out["operating_cashflow"][0], out["net_profit"][0]
        if net != 0:
            out["cash_conversion"] = (ocf / net, "yfinance-derived")
    return out


def _bs_value(bs, row_names: list[str]) -> float | None:
    """First non-null value from the most recent balance-sheet column."""
    if bs is None or getattr(bs, "empty", True):
        return None
    col = bs.columns[0]
    for name in row_names:
        if name in bs.index:
            v = _num(bs.loc[name, col])
            if v is not None:
                return v
    return None


def enrich_from_balance_sheet(ticker_obj, info: dict,
                              metrics: dict[str, tuple[float, str]]) -> None:
    """Fill a still-missing total_debt / ROE from the ``.balance_sheet`` statement.

    Mutates ``metrics`` in place. The financial statements are more complete
    than ``.info``; values are tagged source ``yfinance-balancesheet``.
    """
    try:
        bs = ticker_obj.balance_sheet
    except Exception:
        return

    if "total_debt" not in metrics:
        td = _bs_value(bs, ["Total Debt"])
        if td is not None:
            metrics["total_debt"] = (td, "yfinance-balancesheet")

    if "roe" not in metrics:
        equity = _bs_value(bs, ["Stockholders Equity", "Common Stock Equity"])
        net = _num(info.get("netIncomeToCommon"))
        if equity and equity > 0 and net is not None:
            metrics["roe"] = (net / equity, "yfinance-balancesheet")


def fetch_fundamentals(limit: int | None = None,
                       tickers: list[str] | None = None) -> dict:
    """Fetch fundamentals for companies and store them in ``fundamentals``.

    By default every company is processed; pass ``tickers`` to refresh a
    specific subset (e.g. re-fetching stocks that warned/failed the gate).

    Each stock's raw ``.info`` payload is snapshotted for audit. Returns a
    summary dict: ``{ok, failed, recovered}`` where ``recovered`` counts metrics
    obtained by derivation or balance-sheet fallback rather than direct keys.
    """
    cfg = load_config()
    suffix = cfg["ingest"]["yfinance_suffix"]
    delay = cfg["ingest"]["request_delay_seconds"]
    db_path = resolve(cfg["paths"]["database"])
    now = datetime.now(timezone.utc).isoformat()
    period = "TTM"

    result = {"ok": 0, "failed": 0, "recovered": 0}
    with connect(db_path) as conn:
        if tickers is None:
            tickers = [r["ticker"]
                       for r in conn.execute("SELECT ticker FROM companies")]
            if limit:
                tickers = tickers[:limit]

        for ticker in tickers:
            try:
                tk = yf.Ticker(ticker + suffix)
                info = tk.info
            except Exception:
                result["failed"] += 1
                continue
            if not info or not (info.get("shortName") or info.get("longName")):
                result["failed"] += 1
                continue

            save_snapshot(conn, json.dumps(info, default=str, indent=2),
                          source="yfinance", kind="fundamentals", ticker=ticker)

            metrics = extract_metrics(info)
            # balance-sheet fallback only when .info still left a gap
            if "total_debt" not in metrics or "roe" not in metrics:
                enrich_from_balance_sheet(tk, info, metrics)

            result["recovered"] += sum(
                1 for _, src in metrics.values() if src != "yfinance")
            conn.executemany(
                """INSERT OR REPLACE INTO fundamentals
                   (ticker, period, metric, value, source, fetched_at)
                   VALUES (?,?,?,?,?,?)""",
                [(ticker, period, metric, value, source, now)
                 for metric, (value, source) in metrics.items()],
            )

            conn.execute(
                """UPDATE companies
                   SET market_cap = COALESCE(?, market_cap),
                       sector     = COALESCE(?, sector)
                   WHERE ticker = ?""",
                (info.get("marketCap"), info.get("sector"), ticker))
            result["ok"] += 1
            time.sleep(delay)
    return result
