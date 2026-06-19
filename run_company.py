"""Company Analyzer — analyse any one Indian listed company by name.

Standalone deep-dive (independent of the monthly funnel): give a company name,
it resolves the symbol, fetches the data on demand plus a few sector peers, and
estimates the expected return a small investor can earn over 1m / 6m / 12m / 5y
as Bear/Base/Bull scenario bands, for swing trading and buy-and-hold.

    python run_company.py "Reliance"          # resolve by name (asks if unsure)
    python run_company.py "Tata Motors" --deep # Bull/Bear/Value/Growth debate
    python run_company.py TCS --symbol         # treat the input as a symbol
    python run_company.py "Infosys" --yes      # auto-pick the best name match
    python run_company.py "ITC" --no-ai        # estimates only, no API needed

Numbers are computed in Python; the AI only narrates. Estimates are
probabilistic scenarios, not predictions — a research aid, not advice.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.company import report                    # noqa: E402
from src.company.resolve import resolve_name      # noqa: E402


def _resolve(query: str, assume_symbol: bool, auto_yes: bool) -> tuple[str, str]:
    """Return ``(ticker, display_name)`` or exit with a helpful message."""
    if assume_symbol:
        return query.upper().strip(), query.upper().strip()

    try:
        res = resolve_name(query)
    except Exception as e:                                      # noqa: BLE001
        print(f"Name resolution failed ({e}). Retry with --symbol if you know "
              f"the NSE/BSE ticker, e.g.  python run_company.py {query} --symbol")
        sys.exit(1)

    if res["status"] == "none":
        print(f"No listed Indian company found for '{query}'. Try a fuller name "
              f"or pass the ticker with --symbol.")
        sys.exit(1)

    if res["status"] == "ok" or auto_yes:
        c = res["chosen"] or res["candidates"][0]
        print(f"Resolved '{query}' -> {c['name']} [{c['ticker']}] ({c['symbol']})")
        return c["ticker"], c["name"]

    # ambiguous: prompt the user to choose
    print(f"Multiple matches for '{query}':")
    cands = res["candidates"]
    for i, c in enumerate(cands, 1):
        print(f"  [{i}] {c['name']}  [{c['ticker']}]  ({c['symbol']})")
    try:
        choice = input("Pick a number (or Enter to cancel): ").strip()
    except EOFError:
        choice = ""
    if not choice.isdigit() or not (1 <= int(choice) <= len(cands)):
        print("Cancelled.")
        sys.exit(0)
    c = cands[int(choice) - 1]
    return c["ticker"], c["name"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse one company by name")
    parser.add_argument("query", help="company name (or symbol with --symbol)")
    parser.add_argument("--symbol", action="store_true",
                        help="treat the input as an NSE/BSE ticker, skip lookup")
    parser.add_argument("--yes", action="store_true",
                        help="auto-pick the best name match without prompting")
    parser.add_argument("--deep", action="store_true",
                        help="run the Bull/Bear/Value/Growth + Judge debate")
    parser.add_argument("--no-ai", action="store_true",
                        help="skip the AI narrative (no API key needed)")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch fundamentals and prices even if cached")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore the AI cache and call the API fresh")
    args = parser.parse_args()

    ticker, _name = _resolve(args.query, args.symbol, args.yes)

    print(f"Analysing {ticker} ...")
    try:
        rep = report.analyze_company(
            ticker, refresh=args.refresh,
            ai_mode="deep" if args.deep else "lite",
            no_ai=args.no_ai, use_cache=not args.no_cache)
    except Exception as e:                                      # noqa: BLE001
        print(f"Analysis failed: {e}")
        sys.exit(1)

    print()
    print(report.format_cli(rep))
    print("\nSaved to the database. View it in the dashboard:")
    print("  streamlit run dashboard/app.py   (Company Analyzer tab)")


if __name__ == "__main__":
    main()
