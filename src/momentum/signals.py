"""Momentum signals — deterministic, computed from the existing prices table.

All signals are pure functions. ``compute_signals`` operates on one ticker's
OHLCV DataFrame; ``compute_and_store`` runs the full universe and writes to the
``momentum_signals`` table.

Per AGENTS.md, these numbers are produced by Python and stored — they are
*never* generated or estimated by the AI.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from src.config import load_config, resolve
from src.db.schema import connect


# ─────────────────────── helpers ───────────────────────


def _wilder_rsi(close: np.ndarray, period: int) -> Optional[float]:
    """Wilder's RSI (the standard formulation)."""
    if len(close) < period + 1:
        return None
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                period: int) -> Optional[float]:
    """Wilder's ATR(period) — average true range with Wilder smoothing."""
    if len(close) < period + 1:
        return None
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]),
                   np.abs(low[1:] - close[:-1])))
    if len(tr) < period:
        return None
    atr = tr[:period].mean()
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
    return float(atr)


# ─────────────────────── core ───────────────────────


def compute_signals(prices: pd.DataFrame, w: dict) -> Optional[dict]:
    """Compute all momentum signals for one ticker.

    ``prices`` must be sorted ascending by date and contain columns
    ``open, high, low, close, volume``. Returns ``None`` if the ticker has
    less than ~1 year of trading data (signals would be unreliable).
    """
    n = len(prices)
    if n < w["twelve_months_days"]:
        return None

    close = prices["close"].to_numpy(dtype=float)
    high = prices["high"].to_numpy(dtype=float)
    low = prices["low"].to_numpy(dtype=float)
    volume = prices["volume"].to_numpy(dtype=float)
    current = float(close[-1])

    # --- returns ---
    def _ret(days: int) -> Optional[float]:
        if n <= days or close[-1 - days] <= 0:
            return None
        return float(close[-1] / close[-1 - days] - 1.0)

    mom_1m = _ret(w["one_month_days"])
    mom_6m = _ret(w["six_months_days"])

    # 12-1 momentum: return from 12 months ago to 1 month ago. Excludes the
    # past month to dodge the well-documented short-term reversal effect.
    if (n > w["twelve_months_days"]
            and close[-1 - w["twelve_months_days"]] > 0):
        old = close[-1 - w["twelve_months_days"]]
        recent = close[-1 - w["one_month_days"]]
        mom_12_1 = float(recent / old - 1.0)
    else:
        mom_12_1 = None

    # --- 52-week high distance (<= 0 normally; closer to 0 = better) ---
    look = w["high_52w_days"]
    high_52w = float(close[-look:].max()) if n >= look else float(close.max())
    dist_52w_high = (current - high_52w) / high_52w if high_52w > 0 else None

    # --- volume trend ---
    if n >= w["volume_long_days"]:
        v_short = float(volume[-w["volume_short_days"]:].mean())
        v_long = float(volume[-w["volume_long_days"]:].mean())
        volume_trend = (v_short / v_long) if v_long > 0 else None
    else:
        volume_trend = None

    # --- trend filter (binary): close > SMA50 > SMA200 ---
    if n >= w["ma_long_days"]:
        sma_short = float(close[-w["ma_short_days"]:].mean())
        sma_long = float(close[-w["ma_long_days"]:].mean())
        trend_filter = int(current > sma_short and sma_short > sma_long)
    else:
        trend_filter = None

    rsi = _wilder_rsi(close, w["rsi_period"])
    atr = _wilder_atr(high, low, close, w["atr_period"])

    # --- realised volatility (annualised, from daily log returns) ---
    pos = close > 0
    if pos.all():
        log_ret = np.log(close[1:] / close[:-1])
    else:
        log_ret = np.diff(np.log(np.where(pos, close, np.nan)))
        log_ret = log_ret[~np.isnan(log_ret)]

    def _vol(window: int) -> Optional[float]:
        if len(log_ret) < window:
            return None
        return float(log_ret[-window:].std(ddof=1) * np.sqrt(252))

    vol_1m = _vol(w["vol_short_days"])
    vol_3m = _vol(w["vol_long_days"])

    return {
        "mom_12_1": mom_12_1,
        "mom_6m": mom_6m,
        "mom_1m": mom_1m,
        "dist_52w_high": dist_52w_high,
        "volume_trend": volume_trend,
        "trend_filter": trend_filter,
        "rsi_14": rsi,
        "atr_14": atr,
        "vol_1m": vol_1m,
        "vol_3m": vol_3m,
        "current_price": current,
    }


# ─────────────────────── batch ───────────────────────


def compute_and_store(run_date: Optional[str] = None) -> dict:
    """Compute momentum signals for every company; write to ``momentum_signals``.

    Returns ``{computed, skipped}``. The write is idempotent per ``run_date``.
    """
    cfg = load_config()
    windows = cfg["momentum"]["windows"]
    db_path = resolve(cfg["paths"]["database"])
    if run_date is None:
        run_date = date.today().isoformat()

    summary = {"computed": 0, "skipped": 0}
    rows: list[tuple] = []
    with connect(db_path) as conn:
        tickers = [r[0] for r in conn.execute("SELECT ticker FROM companies")]
        all_prices = pd.read_sql_query(
            "SELECT ticker, date, open, high, low, close, volume "
            "FROM prices ORDER BY ticker, date", conn)

        for ticker, sub in all_prices.groupby("ticker", sort=False):
            sigs = compute_signals(sub.reset_index(drop=True), windows)
            if sigs is None:
                summary["skipped"] += 1
                continue
            rows.append((ticker, run_date,
                         sigs["mom_12_1"], sigs["mom_6m"], sigs["mom_1m"],
                         sigs["dist_52w_high"], sigs["volume_trend"],
                         sigs["trend_filter"], sigs["rsi_14"], sigs["atr_14"],
                         sigs["vol_1m"], sigs["vol_3m"],
                         sigs["current_price"]))
            summary["computed"] += 1

        # tickers with no price history at all
        with_prices = set(all_prices["ticker"].unique())
        summary["skipped"] += sum(1 for t in tickers if t not in with_prices)

        conn.execute("DELETE FROM momentum_signals WHERE run_date=?",
                     (run_date,))
        conn.executemany(
            """INSERT INTO momentum_signals
               (ticker, run_date, mom_12_1, mom_6m, mom_1m, dist_52w_high,
                volume_trend, trend_filter, rsi_14, atr_14, vol_1m, vol_3m,
                current_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return summary
