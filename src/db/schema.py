"""SQLite schema for Personal Warren.

Phase 1 tables (``companies``, ``prices``, ``fundamentals``, ``snapshots``,
``data_quality``, ``news``) are used now. The remaining tables are created up
front as placeholders so later phases can write to them without a migration.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA: dict[str, str] = {
    # ─── Phase 1 ───────────────────────────────────────────
    "companies": """
        CREATE TABLE IF NOT EXISTS companies (
            ticker      TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            sector      TEXT,
            industry    TEXT,
            isin        TEXT,
            market_cap  REAL,
            updated_at  TEXT
        )
    """,
    "prices": """
        CREATE TABLE IF NOT EXISTS prices (
            ticker  TEXT NOT NULL,
            date    TEXT NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            source  TEXT NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """,
    "fundamentals": """
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker      TEXT NOT NULL,
            period      TEXT NOT NULL,      -- e.g. TTM, FY2024, Q1FY2025
            metric      TEXT NOT NULL,
            value       REAL,
            source      TEXT NOT NULL,
            fetched_at  TEXT NOT NULL,
            PRIMARY KEY (ticker, period, metric, source)
        )
    """,
    "snapshots": """
        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT,
            source      TEXT NOT NULL,
            kind        TEXT NOT NULL,      -- universe | prices | fundamentals | news
            path        TEXT NOT NULL,      -- file under data/raw/
            fetched_at  TEXT NOT NULL
        )
    """,
    "data_quality": """
        CREATE TABLE IF NOT EXISTS data_quality (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            check_name  TEXT NOT NULL,
            status      TEXT NOT NULL,      -- pass | warn | fail
            detail      TEXT,
            run_at      TEXT NOT NULL
        )
    """,
    "news": """
        CREATE TABLE IF NOT EXISTS news (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT,
            source      TEXT NOT NULL,
            title       TEXT NOT NULL,
            url         TEXT UNIQUE,
            published   TEXT,
            fetched_at  TEXT NOT NULL
        )
    """,
    # ─── placeholders for later phases ─────────────────────
    "scores": """
        CREATE TABLE IF NOT EXISTS scores (
            ticker          TEXT NOT NULL,
            run_date        TEXT NOT NULL,
            category        TEXT NOT NULL,
            score           REAL,
            weight          REAL,
            weights_version TEXT,
            PRIMARY KEY (ticker, run_date, category)
        )
    """,
    "flags": """
        CREATE TABLE IF NOT EXISTS flags (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker    TEXT NOT NULL,
            run_date  TEXT NOT NULL,
            flag      TEXT NOT NULL,
            severity  TEXT NOT NULL,
            detail    TEXT
        )
    """,
    "theses": """
        CREATE TABLE IF NOT EXISTS theses (
            ticker       TEXT NOT NULL,
            run_date     TEXT NOT NULL,
            bull         TEXT,
            bear         TEXT,
            value_view   TEXT,
            growth_view  TEXT,
            thesis       TEXT,        -- the judge's full markdown thesis
            verdict      TEXT,        -- Strong Buy / Watchlist / Avoid
            confidence   INTEGER,     -- 0-100
            total_score  REAL,        -- deterministic score, for reference
            PRIMARY KEY (ticker, run_date)
        )
    """,
    "portfolio": """
        CREATE TABLE IF NOT EXISTS portfolio (
            ticker       TEXT PRIMARY KEY,
            weight       REAL,
            entry_date   TEXT,
            entry_price  REAL,
            thesis_id    INTEGER
        )
    """,
    "predictions": """
        CREATE TABLE IF NOT EXISTS predictions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_id    INTEGER,
            expected     TEXT,
            horizon      TEXT,
            actual       TEXT,
            reviewed_at  TEXT
        )
    """,
    # ─── Phase 2 ───────────────────────────────────────────
    "rankings": """
        CREATE TABLE IF NOT EXISTS rankings (
            ticker        TEXT NOT NULL,
            run_date      TEXT NOT NULL,
            total_score   REAL,
            rank          INTEGER,
            in_shortlist  INTEGER,      -- 0 / 1
            template      TEXT,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    # ─── Phase 3 ───────────────────────────────────────────
    "ai_cache": """
        CREATE TABLE IF NOT EXISTS ai_cache (
            cache_key   TEXT PRIMARY KEY,   -- sha256(model + messages)
            model       TEXT,
            response    TEXT,
            created_at  TEXT
        )
    """,
    # ─── Legacy Advisor (long-term, dormant under the pivot) ─
    "valuation_advice": """
        CREATE TABLE IF NOT EXISTS valuation_advice (
            ticker            TEXT NOT NULL,
            run_date          TEXT NOT NULL,
            current_price     REAL,
            fair_value        REAL,
            margin_of_safety  REAL,        -- (fair - price) / price
            valuation_verdict TEXT,        -- Undervalued / Fairly Valued / Overvalued
            action            TEXT,        -- Buy Now / Accumulate Gradually / Wait
            target_buy_price  REAL,        -- price that restores the margin of safety
            price_position    REAL,        -- 0-1, where price sits in its 3y range
            current_pe        REAL,
            fair_pe           REAL,
            rationale         TEXT,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    # ─── Phase 4 — Momentum + monthly pipeline ─────────────
    "momentum_signals": """
        CREATE TABLE IF NOT EXISTS momentum_signals (
            ticker          TEXT NOT NULL,
            run_date        TEXT NOT NULL,
            mom_12_1        REAL,    -- 12m return minus most recent 1m return
            mom_6m          REAL,    -- 6-month return
            mom_1m          REAL,    -- raw 1-month return (informational)
            dist_52w_high   REAL,    -- (close - 52w_high) / 52w_high  (<= 0 usually)
            volume_trend    REAL,    -- 20d avg volume / 60d avg volume
            trend_filter    INTEGER, -- 0/1: close > SMA50 > SMA200
            rsi_14          REAL,
            atr_14          REAL,
            vol_1m          REAL,    -- 1-month realised volatility (annualised)
            vol_3m          REAL,    -- 3-month realised volatility
            current_price   REAL,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    "monthly_rankings": """
        CREATE TABLE IF NOT EXISTS monthly_rankings (
            ticker           TEXT NOT NULL,
            run_date         TEXT NOT NULL,
            quality_score    REAL,   -- weighted Quality+Moat+FS (filter input)
            momentum_score   REAL,   -- combined 0-100 momentum percentile
            combined_score   REAL,   -- final score used to rank
            rank             INTEGER,
            in_shortlist     INTEGER,
            quality_passed   INTEGER,
            trend_passed     INTEGER,
            template         TEXT,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    "monthly_advice": """
        CREATE TABLE IF NOT EXISTS monthly_advice (
            ticker             TEXT NOT NULL,
            run_date           TEXT NOT NULL,
            action             TEXT,   -- Take / Hold / Skip
            entry_price        REAL,
            stop_loss          REAL,
            target_price       REAL,
            position_size_pct  REAL,   -- suggested % of portfolio
            holding_days       INTEGER,
            rationale          TEXT,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    "monthly_briefs": """
        CREATE TABLE IF NOT EXISTS monthly_briefs (
            ticker     TEXT NOT NULL,
            run_date   TEXT NOT NULL,
            brief      TEXT,
            model      TEXT,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    # ─── Phase 6 — Learning log (realised outcomes of past picks) ───
    "pick_outcomes": """
        CREATE TABLE IF NOT EXISTS pick_outcomes (
            ticker             TEXT NOT NULL,
            run_date           TEXT NOT NULL,       -- when the pick was made
            review_date        TEXT,                -- when the loop evaluated it
            entry_price        REAL,
            stop_loss          REAL,
            target_price       REAL,
            holding_days       INTEGER,
            exit_date          TEXT,
            exit_price         REAL,
            outcome            TEXT,                -- stop_hit / target_hit / held / not_yet
            actual_return_pct  REAL,
            nifty_return_pct   REAL,
            excess_return_pct  REAL,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    # ─── Phase 5 — Backtest ─────────────────────────────────
    "backtest_runs": """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at               TEXT,
            config_hash          TEXT,
            start_date           TEXT,
            end_date             TEXT,
            months_total         INTEGER,
            months_beating_nifty INTEGER,
            avg_excess_pct       REAL,
            total_return_pct     REAL,
            nifty_return_pct     REAL,
            max_drawdown_pct     REAL
        )
    """,
    "backtest_monthly": """
        CREATE TABLE IF NOT EXISTS backtest_monthly (
            run_id           INTEGER NOT NULL,
            month            TEXT NOT NULL,        -- YYYY-MM
            portfolio_return REAL,
            nifty_return     REAL,
            excess_return    REAL,
            n_holdings       INTEGER,
            holdings_json    TEXT,                  -- JSON list of (ticker, weight)
            PRIMARY KEY (run_id, month)
        )
    """,
    # ─── Company Analyzer — name-driven single-company deep-dive ───
    "company_analysis": """
        CREATE TABLE IF NOT EXISTS company_analysis (
            ticker             TEXT NOT NULL,
            run_date           TEXT NOT NULL,
            name               TEXT,
            sector             TEXT,
            template           TEXT,
            business_summary   TEXT,
            quality_mode       TEXT,        -- relative | absolute
            quality_total      REAL,        -- 0-100 after penalties
            quality_composite  REAL,        -- mean(quality, moat, financial_strength)
            sector_percentile  REAL,        -- 0-100 vs sampled peers (relative mode)
            categories_json    TEXT,        -- {quality, moat, ...} 6-category scores
            flags_json         TEXT,        -- {disqualifiers:[], penalties:[]}
            current_price      REAL,
            entry_price        REAL,
            stop_loss          REAL,
            target_price       REAL,
            swing_ev_pct       REAL,        -- 1-month swing expected value %
            hold_ev_pct        REAL,        -- 1-month buy-and-hold expected value %
            swing_reco         TEXT,        -- Swing trade / Buy & hold 1 month / Avoid for now
            confidence         TEXT,        -- overall data confidence
            ai_mode            TEXT,        -- lite | deep | none
            ai_narrative       TEXT,
            ai_verdict         TEXT,        -- deep mode only
            ai_confidence      INTEGER,     -- deep mode only
            model              TEXT,
            created_at         TEXT,
            PRIMARY KEY (ticker, run_date)
        )
    """,
    "company_estimates": """
        CREATE TABLE IF NOT EXISTS company_estimates (
            ticker          TEXT NOT NULL,
            run_date        TEXT NOT NULL,
            horizon         TEXT NOT NULL,   -- 1m | 6m | 12m | 5y
            days            INTEGER,
            bear            REAL,            -- total-return fractions
            base            REAL,            -- expected (central) return
            bull            REAL,
            prob_positive   REAL,
            expected_value  REAL,
            nifty_base      REAL,
            nifty_alpha     REAL,
            components_json TEXT,
            confidence      TEXT,
            PRIMARY KEY (ticker, run_date, horizon)
        )
    """,
}


def init_db(db_path: str | Path) -> None:
    """Create the database file and all tables if they do not exist."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        for ddl in SCHEMA.values():
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Connection context manager: commits on clean exit, always closes."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
