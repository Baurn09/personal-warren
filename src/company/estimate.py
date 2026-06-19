"""Multi-horizon expected-earnings estimator (deterministic, pure Python).

For each horizon (1m / 6m / 12m / 5y) this produces a Bear / Base / Bull
total-return band, a probability of a positive outcome, and an expected value.
Nothing here predicts a price — every number is a transparent blend of:

* **Empirical** — percentiles of the company's own overlapping H-day historical
  returns (the band *width* and the probability of a gain).
* **Volatility model** — drift ``mu*H`` with spread ``z*sigma*sqrt(H)`` from
  daily log returns (used when the price history is too short for the empirical
  distribution).
* **Fundamental decomposition** — expected annual return ~ earnings growth
  (capped, quality-scaled) + dividend yield + valuation re-rating toward a
  sector-median PE, compounded to the horizon. This *centres* the band.
* **Momentum tilt** — a bounded shift to the central case from the 12-1 momentum
  signal, fading to zero by the 12-month horizon.

The fundamental view moves the centre; the statistical view sets the spread.
Per AGENTS.md, the AI never produces any of these numbers.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

CATEGORIES = ["1m", "6m", "12m", "5y"]


# ─────────────────────── small helpers ───────────────────────


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ─────────────────────── statistical bands ───────────────────────


def empirical_bands(closes: np.ndarray, days: int, sc: dict) -> Optional[dict]:
    """Bear/Base/Bull from overlapping ``days``-horizon historical returns.

    Needs a comfortable number of samples (at least ~20 beyond the horizon)
    or it returns ``None`` so the caller can fall back to the vol model.
    """
    n = len(closes)
    if n < days + 20:
        return None
    fwd = closes[days:] / closes[:-days] - 1.0
    fwd = fwd[np.isfinite(fwd)]
    if len(fwd) < 20:
        return None
    return {
        "bear": float(np.percentile(fwd, sc["bear_percentile"] * 100)),
        "base": float(np.percentile(fwd, sc["base_percentile"] * 100)),
        "bull": float(np.percentile(fwd, sc["bull_percentile"] * 100)),
        "prob_positive": float((fwd > 0).mean()),
        "source": "empirical",
    }


def vol_bands(log_ret: np.ndarray, days: int, z: float) -> Optional[dict]:
    """Bear/Base/Bull from a drift + volatility (log-normal) model."""
    if len(log_ret) < 30:
        return None
    mu = float(np.mean(log_ret))
    sd = float(np.std(log_ret, ddof=1))
    mu_h = mu * days
    sd_h = sd * math.sqrt(days)
    base = math.expm1(mu_h)
    bear = math.expm1(mu_h - z * sd_h)
    bull = math.expm1(mu_h + z * sd_h)
    prob_pos = _normal_cdf(mu_h / sd_h) if sd_h > 0 else (1.0 if mu_h > 0 else 0.0)
    return {"bear": bear, "base": base, "bull": bull,
            "prob_positive": prob_pos, "source": "vol_model"}


# ─────────────────────── fundamental decomposition ───────────────────────


def fundamental_annual_return(metrics: dict, quality_composite: Optional[float],
                              sector_pe: Optional[float], cfg: dict) -> dict:
    """Expected *annual* return from fundamentals.

    annual ~ quality-scaled earnings growth + dividend yield + PE re-rating.
    Returns the annual figure plus its component breakdown and a ``missing``
    flag when no growth signal was available.
    """
    f = cfg["fundamental"]
    cap = float(f["growth_cap"])

    g = _num(metrics.get("earnings_growth"))
    if g is None:
        g = _num(metrics.get("revenue_growth"))
    missing = g is None
    g = 0.0 if g is None else _clamp(g, -cap, cap)

    qfrac = (quality_composite if quality_composite is not None else 50.0) / 100.0
    g_eff = g * (0.5 + 0.5 * qfrac)            # low quality -> trust growth less

    d = _num(metrics.get("dividend_yield"))
    if d is None:
        d = float(f["default_div_yield"])

    pe = _num(metrics.get("pe"))
    spe = _num(sector_pe) or float(f["sector_median_pe_fallback"])
    rr = 0.0
    if pe and pe > 0 and spe and spe > 0:
        years = float(f["pe_rerating_years"])
        rr = (spe / pe) ** (1.0 / years) - 1.0
        rr_cap = float(f["pe_rerating_cap"])
        rr = _clamp(rr, -rr_cap, rr_cap)

    annual = g_eff + d + rr
    return {"annual": annual, "missing": missing,
            "components": {"growth_raw": g, "growth_effective": g_eff,
                           "dividend_yield": d, "pe_rerating": rr}}


# ─────────────────────── momentum tilt ───────────────────────


def momentum_scale(days: int) -> float:
    """1.0 at the 1-month horizon, fading linearly to 0 by 12 months."""
    if days <= 21:
        return 1.0
    if days >= 252:
        return 0.0
    return (252 - days) / (252 - 21)


def momentum_shift(signals: Optional[dict], days: int, cfg: dict) -> float:
    """Bounded shift to the central case from the 12-1 momentum signal."""
    if not signals:
        return 0.0
    mom = _num(signals.get("mom_12_1"))
    if mom is None:
        return 0.0
    unit = math.tanh(mom)                        # squash into (-1, 1)
    trend = signals.get("trend_filter")
    if trend == 1:
        unit = _clamp(unit + 0.15, -1.0, 1.0)    # mild confirmation bonus
    elif trend == 0:
        unit = _clamp(unit - 0.15, -1.0, 1.0)
    return unit * float(cfg["momentum_tilt"]["max_shift"]) * momentum_scale(days)


# ─────────────────────── per-horizon assembly ───────────────────────


def _confidence(n_hist: int, days: int, stat_source: str,
                fund_missing: bool, cfg: dict) -> str:
    if n_hist < int(cfg["min_history_days"]):
        return "low"
    if days >= 1260 or stat_source == "vol_model" or fund_missing:
        return "medium"
    return "high"


def estimate_horizon(closes: np.ndarray, log_ret: np.ndarray, days: int,
                     metrics: dict, quality_composite: Optional[float],
                     signals: Optional[dict], sector_pe: Optional[float],
                     nifty_closes: Optional[np.ndarray], cfg: dict,
                     blend: dict) -> Optional[dict]:
    """Assemble the Bear/Base/Bull estimate for one horizon."""
    sc = cfg["scenario"]
    stat = empirical_bands(closes, days, sc) or vol_bands(log_ret, days, sc["z_band"])
    if stat is None:
        return None

    fund = fundamental_annual_return(metrics, quality_composite, sector_pe, cfg)
    fund_base = math.expm1(math.log1p(fund["annual"]) * (days / 252.0))

    w_emp = float(blend["empirical"])
    w_fund = float(blend["fundamental"])
    shift = momentum_shift(signals, days, cfg)

    center = w_emp * stat["base"] + w_fund * fund_base + shift
    down = max(0.0, stat["base"] - stat["bear"])
    up = max(0.0, stat["bull"] - stat["base"])
    bear = center - down
    bull = center + up

    # probability of a gain, re-centred on the blended expectation
    sd = (up + down) / (2.0 * sc["z_band"]) if (up + down) > 0 else 0.0
    prob_pos = _normal_cdf(center / sd) if sd > 0 else stat["prob_positive"]

    nifty_base = None
    nifty_alpha = None
    if nifty_closes is not None:
        nb = empirical_bands(nifty_closes, days, sc)
        if nb is not None:
            nifty_base = nb["base"]
            nifty_alpha = center - nb["base"]

    return {
        "days": days,
        "bear": bear,
        "base": center,
        "bull": bull,
        "prob_positive": prob_pos,
        "expected_value": center,
        "nifty_base": nifty_base,
        "nifty_alpha": nifty_alpha,
        "confidence": _confidence(len(closes), days, stat["source"],
                                  fund["missing"], cfg),
        "components": {
            "stat_source": stat["source"],
            "stat_base": stat["base"],
            "fundamental_base": fund_base,
            "fundamental_annual": fund["annual"],
            "fundamental_parts": fund["components"],
            "momentum_shift": shift,
            "blend": {"empirical": w_emp, "fundamental": w_fund},
        },
    }


def estimate_all(prices: pd.DataFrame, metrics: dict,
                 quality_composite: Optional[float], signals: Optional[dict],
                 sector_pe: Optional[float],
                 nifty_prices: Optional[pd.DataFrame], cfg: dict) -> dict:
    """Estimate every configured horizon. Returns ``{horizon: estimate|None}``."""
    closes = prices["close"].to_numpy(dtype=float)
    closes = closes[np.isfinite(closes) & (closes > 0)]
    if len(closes) > 1:
        log_ret = np.log(closes[1:] / closes[:-1])
        log_ret = log_ret[np.isfinite(log_ret)]
    else:
        log_ret = np.array([])

    nifty_closes = None
    if nifty_prices is not None and not nifty_prices.empty:
        nc = nifty_prices["close"].to_numpy(dtype=float)
        nifty_closes = nc[np.isfinite(nc) & (nc > 0)]

    out: dict = {}
    for horizon in CATEGORIES:
        days = int(cfg["horizons"][horizon])
        blend = cfg["blend"][horizon]
        out[horizon] = estimate_horizon(
            closes, log_ret, days, metrics, quality_composite, signals,
            sector_pe, nifty_closes, cfg, blend)
    return out
