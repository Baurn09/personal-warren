"""Unit tests for the valuation advisor.

Runnable two ways:
    pytest tests/test_advisor.py
    python tests/test_advisor.py        (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.advisor.valuation import advise      # noqa: E402
from src.config import load_config            # noqa: E402

ACFG = load_config()["advisor"]


def test_undervalued_is_buy_now():
    # fair P/E = 13 + 0 + 7 = 20, EPS 10 -> fair value 200; price 80 is cheap
    a = advise({"eps": 10.0, "earnings_growth": 0.0}, 80, 70, 300,
               quality=90, moat=90, acfg=ACFG)
    assert a["verdict"] == "Undervalued"
    assert a["action"] == "Buy Now"
    assert a["margin_of_safety"] > 0


def test_overvalued_is_wait():
    a = advise({"eps": 10.0, "earnings_growth": 0.0}, 600, 200, 620,
               quality=90, moat=90, acfg=ACFG)
    assert a["verdict"] == "Overvalued"
    assert a["action"] == "Wait"
    assert a["target_buy_price"] < a["current_price"]


def test_fairly_valued_is_accumulate():
    # fair P/E = 12 + 0 + 5 = 17 (quality 90, moat 90 -> strong franchise);
    # EPS 10 -> fair value 170; price 170 -> margin of safety ~ 0 (fairly valued).
    a = advise({"eps": 10.0, "earnings_growth": 0.0}, 170, 150, 220,
               quality=90, moat=90, acfg=ACFG)
    assert a["verdict"] == "Fairly Valued"
    assert a["action"] == "Accumulate Gradually"


def test_no_earnings_is_no_call():
    a = advise({"eps": None}, 100, 80, 120, quality=90, moat=90, acfg=ACFG)
    assert a["action"] == "No call"
    assert a["fair_value"] is None


def test_growth_raises_fair_value():
    base = advise({"eps": 10.0, "earnings_growth": 0.0}, 200, 1, 2,
                  quality=50, moat=50, acfg=ACFG)
    grown = advise({"eps": 10.0, "earnings_growth": 0.20}, 200, 1, 2,
                   quality=50, moat=50, acfg=ACFG)
    assert grown["fair_value"] > base["fair_value"]


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
        except Exception as e:   # noqa: BLE001
            failed += 1
            print(f"ERROR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
