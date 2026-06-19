"""Tests for the multi-horizon estimator — pure functions, no DB or network."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.company import estimate as E   # noqa: E402

_CA = {
    "horizons": {"1m": 21, "6m": 126, "12m": 252, "5y": 1260},
    "scenario": {"bear_percentile": 0.20, "base_percentile": 0.50,
                 "bull_percentile": 0.80, "z_band": 0.84},
    "blend": {"1m": {"empirical": 0.90, "fundamental": 0.10},
              "6m": {"empirical": 0.70, "fundamental": 0.30},
              "12m": {"empirical": 0.50, "fundamental": 0.50},
              "5y": {"empirical": 0.15, "fundamental": 0.85}},
    "fundamental": {"growth_cap": 0.25, "default_div_yield": 0.012,
                    "pe_rerating_years": 5.0, "pe_rerating_cap": 0.10,
                    "sector_median_pe_fallback": 22.0},
    "momentum_tilt": {"max_shift": 0.04},
    "min_history_days": 252,
}


def _series(n=1400, drift=0.0005, vol=0.012, seed=1):
    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(n)
    return 100.0 * np.cumprod(1.0 + rets)


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_empirical_bands_ordering():
    closes = _series()
    b = E.empirical_bands(closes, 21, _CA["scenario"])
    assert b is not None
    assert b["bear"] <= b["base"] <= b["bull"]
    assert 0.0 <= b["prob_positive"] <= 1.0


def test_empirical_bands_insufficient():
    closes = _series(n=30)
    assert E.empirical_bands(closes, 252, _CA["scenario"]) is None


def test_vol_bands_ordering_and_drift():
    closes = _series(drift=0.001, vol=0.01)
    log_ret = np.log(closes[1:] / closes[:-1])
    v = E.vol_bands(log_ret, 21, 0.84)
    assert v is not None
    assert v["bear"] <= v["base"] <= v["bull"]
    assert v["base"] > 0          # positive drift -> positive central case


def test_fundamental_decomposition():
    m = {"earnings_growth": 0.20, "dividend_yield": 0.012, "pe": 20.0}
    out = E.fundamental_annual_return(m, quality_composite=50.0,
                                      sector_pe=20.0, cfg=_CA)
    # g_eff = 0.20 * (0.5 + 0.5*0.5) = 0.15 ; rr = 0 (pe == sector_pe)
    assert not out["missing"]
    assert _approx(out["annual"], 0.15 + 0.012, tol=1e-6)


def test_fundamental_growth_capped():
    m = {"earnings_growth": 0.95}
    out = E.fundamental_annual_return(m, quality_composite=100.0,
                                      sector_pe=22.0, cfg=_CA)
    # growth clamped to 0.25, quality 100 -> g_eff = 0.25 * 1.0
    assert out["components"]["growth_raw"] == 0.25


def test_fundamental_missing_growth():
    out = E.fundamental_annual_return({}, quality_composite=None,
                                      sector_pe=None, cfg=_CA)
    assert out["missing"] is True


def test_momentum_scale_monotonic():
    assert E.momentum_scale(21) == 1.0
    assert E.momentum_scale(252) == 0.0
    assert 0.0 < E.momentum_scale(126) < 1.0


def test_momentum_shift_sign_and_bound():
    up = E.momentum_shift({"mom_12_1": 0.5, "trend_filter": 1}, 21, _CA)
    down = E.momentum_shift({"mom_12_1": -0.5, "trend_filter": 0}, 21, _CA)
    assert up > 0 and down < 0
    assert abs(up) <= _CA["momentum_tilt"]["max_shift"] + 1e-12
    # fades to zero at the 12-month horizon
    assert E.momentum_shift({"mom_12_1": 0.5, "trend_filter": 1}, 252, _CA) == 0.0


def test_estimate_horizon_ordering():
    closes = _series()
    log_ret = np.log(closes[1:] / closes[:-1])
    e = E.estimate_horizon(
        closes, log_ret, 21, {"earnings_growth": 0.15, "pe": 20.0},
        quality_composite=60.0, signals={"mom_12_1": 0.2, "trend_filter": 1},
        sector_pe=22.0, nifty_closes=None, cfg=_CA, blend=_CA["blend"]["1m"])
    assert e["bear"] <= e["base"] <= e["bull"]
    assert 0.0 <= e["prob_positive"] <= 1.0
    assert e["expected_value"] == e["base"]


def test_estimate_all_horizons_present():
    px = pd.DataFrame({"close": _series()})
    out = E.estimate_all(px, {"earnings_growth": 0.12, "pe": 25.0},
                         quality_composite=55.0, signals=None,
                         sector_pe=22.0, nifty_prices=None, cfg=_CA)
    for h in ("1m", "6m", "12m", "5y"):
        assert h in out and out[h] is not None
        assert out[h]["bear"] <= out[h]["base"] <= out[h]["bull"]
    # 5-year horizon is never high-confidence (fundamental snapshot caveat)
    assert out["5y"]["confidence"] in ("medium", "low")


def test_estimate_short_history_low_confidence():
    # With < 1y of history the long horizon falls back to the vol model and is
    # flagged low-confidence rather than silently dropped.
    px = pd.DataFrame({"close": _series(n=200)})
    out = E.estimate_all(px, {"earnings_growth": 0.10}, 50.0, None, 22.0,
                         None, _CA)
    assert out["1m"] is not None
    assert out["5y"]["confidence"] == "low"


def test_estimate_too_short_returns_none():
    # Fewer than ~30 days: not even the vol model can run.
    px = pd.DataFrame({"close": _series(n=15)})
    out = E.estimate_all(px, {"earnings_growth": 0.10}, 50.0, None, 22.0,
                         None, _CA)
    assert out["1m"] is None


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
