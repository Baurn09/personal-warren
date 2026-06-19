"""Resolve a company *name* to a listed NSE/BSE symbol.

Candidates come from the Yahoo Finance search endpoint (free, no key) and, when
present, a fuzzy match against the cached ``companies`` table. NSE (``.NS``) is
preferred over BSE (``.BO``) for the same company. If the best match is clearly
ahead it is chosen automatically; otherwise the caller is given the top few to
pick from.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional

from src.ingest.http import make_session

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"


def _similarity(query: str, name: str) -> float:
    return SequenceMatcher(None, query.lower().strip(), (name or "").lower()).ratio()


def search_candidates(query: str, session=None, limit: int = 10) -> list[dict]:
    """Query Yahoo Finance search for Indian equities matching ``query``.

    Returns a list of ``{symbol, ticker, name, exchange}`` dicts (network call).
    """
    session = session or make_session()
    params = {"q": query, "quotesCount": limit, "newsCount": 0,
              "enableFuzzyQuery": "true"}
    resp = session.get(SEARCH_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    out: list[dict] = []
    for q in data.get("quotes", []):
        sym = q.get("symbol", "") or ""
        if q.get("quoteType") != "EQUITY":
            continue
        if not (sym.endswith(".NS") or sym.endswith(".BO")):
            continue
        out.append({
            "symbol": sym,
            "ticker": sym.rsplit(".", 1)[0],
            "name": q.get("shortname") or q.get("longname") or sym,
            "exchange": q.get("exchange") or "",
        })
    return out


def rank_candidates(query: str, candidates: list[dict]) -> list[dict]:
    """Dedupe (preferring NSE), score by name similarity, and sort best-first.

    Pure function — no network — so it is unit-testable with a stub list.
    """
    by_ticker: dict[str, dict] = {}
    for c in candidates:
        t = c["ticker"]
        prev = by_ticker.get(t)
        # prefer the NSE listing when the same company appears on both exchanges
        if prev is None or (not prev["symbol"].endswith(".NS")
                            and c["symbol"].endswith(".NS")):
            by_ticker[t] = c

    ranked = []
    for c in by_ticker.values():
        c = dict(c)
        c["score"] = _similarity(query, c["name"])
        if c["symbol"].endswith(".NS"):
            c["score"] += 0.05            # small tie-break toward NSE
        ranked.append(c)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def resolve_name(query: str, candidates: Optional[list[dict]] = None,
                 session=None) -> dict:
    """Resolve ``query`` to a single symbol or a short ambiguous shortlist.

    Returns ``{status, chosen, candidates}`` where ``status`` is ``ok`` (a clear
    winner in ``chosen``), ``ambiguous`` (pick from ``candidates``), or ``none``.
    """
    if candidates is None:
        candidates = search_candidates(query, session=session)
    ranked = rank_candidates(query, candidates)
    if not ranked:
        return {"status": "none", "chosen": None, "candidates": []}

    top = ranked[0]
    clear = (len(ranked) == 1
             or top["score"] >= 0.90
             or (top["score"] - ranked[1]["score"]) >= 0.20)
    if clear:
        return {"status": "ok", "chosen": top, "candidates": ranked[:5]}
    return {"status": "ambiguous", "chosen": None, "candidates": ranked[:5]}
