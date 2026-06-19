"""In-process monthly job — runs the Quality-Momentum pipeline end-to-end.

Each step is isolated so a single failure does not abort the rest of the
run (e.g. OpenRouter rate limits should not block the deterministic scoring
that already succeeded). Step toggles live in ``settings.yaml`` under the
``scheduler.steps`` block.

The scheduler intentionally does NOT run ``run_pipeline.py`` by default —
data ingest is slow (~30 minutes for the full Nifty 500), uses external
APIs that can rate-limit, and benefits from being run manually so failures
are visible. Enable ``scheduler.steps.pipeline`` to opt in.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from src.config import load_config

log = logging.getLogger("warren.scheduler")


# ─────────────────────── step runners ───────────────────────


def _step(name: str, fn: Callable[[], dict]) -> dict:
    """Run one step, logging its summary and absorbing exceptions."""
    started = time.monotonic()
    log.info("STEP %s starting", name)
    try:
        result = fn()
        # Some ingest steps (universe, news) return a bare int count rather
        # than a dict — normalise so the **result spread below never fails.
        if not isinstance(result, dict):
            result = {"result": result} if result is not None else {}
    except Exception as exc:                                # noqa: BLE001
        elapsed = time.monotonic() - started
        log.exception("STEP %s FAILED after %.1fs: %s", name, elapsed, exc)
        return {"step": name, "ok": False, "error": str(exc)}
    elapsed = time.monotonic() - started
    log.info("STEP %s done in %.1fs: %s", name, elapsed, result)
    return {"step": name, "ok": True, "elapsed_seconds": round(elapsed, 1),
            **result}


def run_monthly_pipeline() -> dict:
    """Run the in-process monthly pipeline. Returns the per-step summary list."""
    cfg = load_config()
    steps_cfg = cfg.get("scheduler", {}).get("steps", {})

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("Monthly pipeline starting at %s UTC", started_at)

    results: list[dict] = []

    if steps_cfg.get("pipeline"):
        from src.ingest.universe import fetch_universe
        from src.ingest.prices import fetch_prices
        from src.ingest.fundamentals import fetch_fundamentals
        from src.ingest.news import fetch_news
        from src.ingest.reconcile import run_quality_gate
        results.append(_step("ingest.universe", fetch_universe))
        results.append(_step("ingest.fundamentals", fetch_fundamentals))
        results.append(_step("ingest.prices", fetch_prices))
        results.append(_step("ingest.news", fetch_news))
        results.append(_step("ingest.quality_gate", run_quality_gate))

    if steps_cfg.get("scoring", True):
        from src.momentum.signals import compute_and_store
        from src.screen.funnel import run_funnel
        from src.screen.monthly_funnel import run_monthly_funnel
        from src.advisor.monthly import run_monthly_advisor
        results.append(_step("scoring.funnel", run_funnel))
        results.append(_step("scoring.momentum_signals", compute_and_store))
        results.append(_step("scoring.monthly_funnel", run_monthly_funnel))
        results.append(_step("scoring.monthly_advisor", run_monthly_advisor))

    if steps_cfg.get("briefs", True):
        from src.agents.monthly_brief import run_monthly_briefs
        results.append(_step("briefs.monthly", run_monthly_briefs))

    if steps_cfg.get("review", True):
        from src.learning.loop import review_outcomes
        results.append(_step("learning.review_outcomes", review_outcomes))

    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("Monthly pipeline finished at %s UTC", finished_at)
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": results,
        "ok": all(r.get("ok") for r in results),
    }


# ─────────────────────── scheduler ───────────────────────


def start_scheduler(run_now: bool = False) -> None:
    """Start a BackgroundScheduler that runs the monthly job on cron.

    Blocks the calling thread (the caller normally runs this from a script
    that should stay alive). Set ``run_now=True`` to also fire the job once
    on startup — useful for smoke-testing.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    cfg = load_config()
    scfg = cfg.get("scheduler", {})
    tz = scfg.get("timezone", "Asia/Kolkata")
    cron = scfg.get("monthly_cron", {"day": "1", "hour": "6", "minute": "30"})

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s")

    scheduler = BlockingScheduler(timezone=tz)
    trigger = CronTrigger(day=str(cron.get("day", "1")),
                          hour=str(cron.get("hour", "6")),
                          minute=str(cron.get("minute", "30")),
                          timezone=tz)
    scheduler.add_job(run_monthly_pipeline, trigger=trigger,
                      id="monthly-pipeline", name="Personal Warren monthly run",
                      max_instances=1, coalesce=True, misfire_grace_time=3600)

    if run_now:
        log.info("Running the monthly pipeline once now (run_now=True) ...")
        run_monthly_pipeline()

    next_fire = scheduler.get_job("monthly-pipeline").next_run_time
    log.info("Scheduler started. Cron: day=%s hour=%s minute=%s (tz=%s).",
             cron.get("day"), cron.get("hour"), cron.get("minute"), tz)
    if next_fire is not None:
        log.info("Next fire: %s", next_fire.isoformat())
    log.info("Ctrl-C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
