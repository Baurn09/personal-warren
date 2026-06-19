"""Unit tests for the Monthly Brief dossier builder.

API calls are NOT exercised — those depend on a live OpenRouter key and are
verified by running `python run_briefs.py --limit 1`.

Runnable two ways:
    pytest tests/test_monthly_brief.py
    python tests/test_monthly_brief.py        (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.monthly_brief import build_brief_dossier   # noqa: E402


def _pick(**overrides) -> dict:
    base = {
        "ticker": "ADANIPOWER",
        "name": "Adani Power",
        "sector": "Utilities",
        "rank": 3,
        "n_shortlist": 15,
        "sector_picks_count": 2,
        "quality_score": 72.0,
        "momentum_score": 84.5,
        "signals": {
            "mom_12_1": 0.42, "mom_6m": 0.18, "dist_52w_high": -0.07,
            "volume_trend": 1.18, "trend_filter": 1, "rsi_14": 58.0,
            "atr_14": 6.4, "vol_1m": 0.38, "vol_3m": 0.34,
            "current_price": 540.0,
        },
        "plan": {
            "entry_price": 540.0, "stop_loss": 502.0,
            "target_price": 605.0, "position_size_pct": 5.5,
        },
        "run_date": "2026-05-29",
    }
    base.update(overrides)
    return base


def test_dossier_contains_all_required_blocks():
    d = build_brief_dossier(_pick())
    for needle in ("COMPANY: Adani Power",
                   "Sector: Utilities",
                   "MOMENTUM SIGNALS",
                   "QUALITY GUARDRAIL",
                   "ADVISOR'S PLAN",
                   "21-day horizon",
                   "12-1 return : +42.0%",
                   "6-month return : +18.0%",
                   "RSI(14) : 58",
                   "Trend filter : above 50 & 200 DMA",
                   "Volume trend (20d/60d) : 1.18x"):
        assert needle in d, f"missing in dossier: {needle!r}\n{d}"


def test_dossier_includes_stop_and_target_percentages():
    d = build_brief_dossier(_pick())
    # stop -7.0% and target +12.0% relative to entry 540
    assert "Stop Rs 502" in d
    assert "(-7.0%)" in d
    assert "Target Rs 605" in d
    assert "(+12.0%)" in d
    assert "Position size : 5.5%" in d


def test_dossier_marks_below_trend_when_filter_is_zero():
    d = build_brief_dossier(_pick(signals={
        "mom_12_1": 0.10, "mom_6m": 0.05, "dist_52w_high": -0.18,
        "volume_trend": 0.9, "trend_filter": 0, "rsi_14": 40.0,
        "atr_14": 3.0, "vol_1m": 0.22, "vol_3m": 0.20, "current_price": 100.0,
    }))
    assert "below trend" in d
    assert "above 50 & 200 DMA" not in d


def test_dossier_handles_missing_optional_fields_gracefully():
    d = build_brief_dossier(_pick(
        sector=None,
        sector_picks_count=0,
        quality_score=None,
        momentum_score=None,
        signals={"mom_12_1": None, "mom_6m": None, "dist_52w_high": None,
                 "volume_trend": None, "trend_filter": None, "rsi_14": None,
                 "atr_14": None, "vol_1m": None, "vol_3m": None,
                 "current_price": None},
    ))
    # Should still produce a non-empty string with the framing scaffolding.
    assert "ADVISOR'S PLAN" in d
    assert "12-1 return : n/a" in d
    assert "RSI(14) : n/a" in d
    assert "Sector: n/a" in d
    assert "no peer picks this month" in d


def test_dossier_does_not_invent_unsupported_numbers():
    """Smoke check: nothing in the dossier should expose 'None' or 'nan' tokens."""
    d = build_brief_dossier(_pick(signals={
        "mom_12_1": None, "mom_6m": 0.18, "dist_52w_high": -0.07,
        "volume_trend": 1.18, "trend_filter": 1, "rsi_14": 58.0,
        "atr_14": 6.4, "vol_1m": float("nan"), "vol_3m": 0.34,
        "current_price": 540.0,
    }))
    assert "None" not in d
    assert "nan" not in d.lower()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL   {t.__name__}: {e}")
        except Exception as e:                                # noqa: BLE001
            failed += 1
            print(f"ERROR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
