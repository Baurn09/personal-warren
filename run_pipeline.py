"""Personal Warren — Phase 1 data pipeline runner.

Initialises the database, ingests the Nifty 500 universe, prices, fundamentals
and news, then runs the data-quality gate.

Usage:
    python run_pipeline.py                       # full run
    python run_pipeline.py --limit 10            # quick test on 10 stocks
    python run_pipeline.py --skip-prices         # skip the slow price fetch
    python run_pipeline.py --universe-file x.csv # load constituents from a file
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# allow `python run_pipeline.py` from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config, resolve          # noqa: E402
from src.db.schema import init_db                     # noqa: E402
from src.ingest import fundamentals, news, prices, reconcile, universe  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Warren data pipeline")
    parser.add_argument("--limit", type=int, default=None,
                        help="limit number of stocks (quick test run)")
    parser.add_argument("--skip-prices", action="store_true",
                        help="skip price-history ingestion")
    parser.add_argument("--skip-news", action="store_true",
                        help="skip news ingestion")
    parser.add_argument("--universe-file", default=None,
                        help="load Nifty 500 constituents from a local CSV")
    args = parser.parse_args()

    db_path = resolve(load_config()["paths"]["database"])

    print("[1/6] Initialising database ...")
    init_db(db_path)

    print("[2/6] Fetching Nifty 500 universe ...")
    try:
        n = universe.fetch_universe(local_file=args.universe_file)
        print(f"      {n} companies")
    except Exception as exc:
        print(f"      ERROR: {exc}")
        print("      If NSE blocked the request, download the NIFTY 500 CSV")
        print("      manually and re-run with --universe-file <path>.")
        return

    print("[3/6] Fetching fundamentals ...")
    f = fundamentals.fetch_fundamentals(limit=args.limit)
    print(f"      ok={f['ok']} failed={f['failed']} "
          f"recovered_metrics={f['recovered']}")

    if args.skip_prices:
        print("[4/6] Skipping prices (--skip-prices)")
    else:
        print("[4/6] Fetching prices ...")
        p = prices.fetch_prices(limit=args.limit)
        print(f"      ok={p['ok']} failed={p['failed']} rows={p['rows']}")

    if args.skip_news:
        print("[5/6] Skipping news (--skip-news)")
    else:
        print("[5/6] Fetching news ...")
        print(f"      {news.fetch_news()} items")

    print("[6/6] Running data-quality gate ...")
    q = reconcile.run_quality_gate()
    print(f"      pass={q['pass']} warn={q['warn']} fail={q['fail']}")

    print("\nDone. Inspect the data with:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
