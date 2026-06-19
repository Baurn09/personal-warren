"""Tests for name->symbol resolution — pure ranking logic, no network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.company import resolve as R   # noqa: E402


def _c(symbol, name):
    return {"symbol": symbol, "ticker": symbol.rsplit(".", 1)[0],
            "name": name, "exchange": ""}


def test_prefers_nse_over_bse():
    cands = [_c("RELIANCE.BO", "Reliance Industries Ltd"),
             _c("RELIANCE.NS", "Reliance Industries Ltd")]
    ranked = R.rank_candidates("Reliance Industries", cands)
    assert len(ranked) == 1                       # de-duplicated by ticker
    assert ranked[0]["symbol"].endswith(".NS")


def test_best_match_first():
    cands = [_c("TATAMOTORS.NS", "Tata Motors Ltd"),
             _c("TATAPOWER.NS", "Tata Power Co Ltd"),
             _c("TATASTEEL.NS", "Tata Steel Ltd")]
    ranked = R.rank_candidates("Tata Motors", cands)
    assert ranked[0]["ticker"] == "TATAMOTORS"


def test_resolve_ok_when_clear_winner():
    cands = [_c("INFY.NS", "Infosys Ltd"),
             _c("TCS.NS", "Tata Consultancy Services Ltd")]
    res = R.resolve_name("Infosys", candidates=cands)
    assert res["status"] == "ok"
    assert res["chosen"]["ticker"] == "INFY"


def test_resolve_ambiguous_close_scores():
    cands = [_c("BAJFINANCE.NS", "Bajaj Finance Ltd"),
             _c("BAJAJFINSV.NS", "Bajaj Finserv Ltd")]
    res = R.resolve_name("Bajaj Fin", candidates=cands)
    assert res["status"] == "ambiguous"
    assert len(res["candidates"]) >= 2


def test_resolve_none_when_empty():
    res = R.resolve_name("Nonexistent", candidates=[])
    assert res["status"] == "none"


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
