"""Personal Warren — monthly brief generator.

Produces a ~100-word LLM brief (catalyst + 30-day risk) per shortlist pick
using OpenRouter. Cached, so re-runs are free; ~15 calls/month total.

Run `python run_scoring.py` first. Usage:
    python run_briefs.py
    python run_briefs.py --limit 5         # smoke-test on the top 5
    python run_briefs.py --no-cache        # force fresh calls
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.agents.monthly_brief import run_monthly_briefs   # noqa: E402
from src.config import load_config, resolve               # noqa: E402
from src.db.schema import init_db                         # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate one ~100-word brief "
                                             "per monthly shortlist pick.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the top-N picks (smoke test).")
    ap.add_argument("--no-cache", action="store_true",
                    help="Bypass the AI response cache.")
    args = ap.parse_args()

    db_path = resolve(load_config()["paths"]["database"])
    if not Path(db_path).exists():
        print("No database found. Run:  python run_pipeline.py  and  "
              "python run_scoring.py")
        return
    init_db(db_path)

    print("Generating monthly briefs (one ~100-word note per pick)")
    print("Cached responses cost nothing; uncached calls hit OpenRouter.")
    print()

    summary = run_monthly_briefs(limit=args.limit, use_cache=not args.no_cache)
    if summary.get("error"):
        print(f"ERROR: {summary['error']}")
        return

    print()
    print(f"Picks         : {summary['picks']}")
    print(f"Completed     : {summary['completed']}")
    print(f"Errors        : {summary['errors']}")
    print(f"API calls     : {summary.get('api_calls', 0)}   "
          f"(cache hits {summary.get('cache_hits', 0)})")
    print(f"Tokens used   : {summary.get('tokens_used', 0)}")
    if summary.get("stopped_early"):
        print("Stopped early — re-run tomorrow to finish the remaining picks.")
    print()
    print("View briefs:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
