"""1-month swing-trade vs buy-and-hold expected value.

The swing side uses the deterministic entry/stop/target plan
(``advisor.monthly.compute_plan``) and a **path simulation** over the company's
own price history: for every historical start day, look ahead one month and
record whether the target was hit before the stop. The empirical hit rates give
an honest expected value for the trade, compared against simply holding for a
month. Pure Python — no AI, no prediction.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def simulate_swing(prices: pd.DataFrame, entry: float, stop: float,
                   target: float, horizon_days: int) -> Optional[dict]:
    """Empirically estimate target/stop hit rates over a rolling window.

    ``entry/stop/target`` are today's plan levels; their *percentages* are
    replayed from each historical start day using that day's close, checking the
    intraday high/low path over the next ``horizon_days``. If the target and the
    stop are both touched on the same day we conservatively assume the stop hit
    first. Returns ``None`` if there is not enough history.
    """
    if entry <= 0:
        return None
    stop_pct = stop / entry - 1.0          # negative
    target_pct = target / entry - 1.0      # positive

    close = prices["close"].to_numpy(dtype=float)
    high = prices["high"].to_numpy(dtype=float)
    low = prices["low"].to_numpy(dtype=float)
    n = len(close)
    if n < horizon_days + 20:
        return None

    n_target = n_stop = n_neither = 0
    neither_returns: list[float] = []

    for i in range(0, n - horizon_days):
        e = close[i]
        if not math.isfinite(e) or e <= 0:
            continue
        tgt = e * (1.0 + target_pct)
        stp = e * (1.0 + stop_pct)
        outcome = None
        for j in range(i + 1, i + horizon_days + 1):
            if low[j] <= stp:               # stop checked first (pessimistic)
                outcome = "stop"
                break
            if high[j] >= tgt:
                outcome = "target"
                break
        if outcome == "target":
            n_target += 1
        elif outcome == "stop":
            n_stop += 1
        else:
            n_neither += 1
            neither_returns.append(close[i + horizon_days] / e - 1.0)

    total = n_target + n_stop + n_neither
    if total == 0:
        return None
    p_t = n_target / total
    p_s = n_stop / total
    p_n = n_neither / total
    mean_neither = float(np.mean(neither_returns)) if neither_returns else 0.0
    ev = p_t * target_pct + p_s * stop_pct + p_n * mean_neither
    return {
        "p_target": p_t,
        "p_stop": p_s,
        "p_neither": p_n,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "mean_neither_return": mean_neither,
        "ev": ev,
        "samples": total,
    }


def swing_vs_hold(prices: pd.DataFrame, plan: dict,
                  hold_base_1m: Optional[float], horizon_days: int) -> dict:
    """Compare the 1-month swing EV against the 1-month buy-and-hold EV."""
    swing = simulate_swing(prices, plan["entry_price"], plan["stop_loss"],
                           plan["target_price"], horizon_days)
    swing_ev = swing["ev"] if swing else None
    hold_ev = hold_base_1m

    if swing_ev is None and hold_ev is None:
        reco = "Insufficient data"
    elif max(swing_ev or -1.0, hold_ev or -1.0) <= 0:
        reco = "Avoid for now"
    elif (swing_ev or -1.0) >= (hold_ev or -1.0):
        reco = "Swing trade"
    else:
        reco = "Buy & hold 1 month"

    edge = None
    if swing_ev is not None and hold_ev is not None:
        edge = swing_ev - hold_ev

    return {"swing": swing, "swing_ev": swing_ev, "hold_ev": hold_ev,
            "edge": edge, "recommendation": reco}
