"""Personal Warren — Phase 3 AI agent runner.

Runs the Bull / Bear / Value / Growth debate + Judge over the funnel shortlist
and writes one thesis per stock to the `theses` table.

Needs an OpenRouter API key in a .env file (copy .env.example to .env first).
Run run_pipeline.py and run_scoring.py before this.

Usage:
    python run_agents.py --limit 3      # test on the top 3 stocks first
    python run_agents.py                # full shortlist
    python run_agents.py --no-cache     # ignore cached AI responses
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.agents.orchestrator import run_agents      # noqa: E402
from src.config import load_config, resolve         # noqa: E402
from src.db.schema import init_db                   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Warren AI agents")
    parser.add_argument("--limit", type=int, default=None,
                        help="run only the first N shortlisted stocks")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore cached AI responses and call the API fresh")
    args = parser.parse_args()

    db_path = resolve(load_config()["paths"]["database"])
    if not Path(db_path).exists():
        print("No database found. Run the earlier phases first:")
        print("    python run_pipeline.py    (Phase 1)")
        print("    python run_scoring.py     (Phase 2)")
        return
    init_db(db_path)

    print("Running AI agents over the funnel shortlist ...")
    try:
        s = run_agents(limit=args.limit, use_cache=not args.no_cache)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        return

    print(f"\n  shortlist      : {s['shortlist']}")
    print(f"  theses written : {s['completed']}")
    print(f"  errors         : {s['errors']}")
    print(f"  API calls      : {s['api_calls']}   cache hits: {s['cache_hits']}")
    print(f"  tokens used    : {s['tokens_used']:,}")
    print("\nView the theses:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
