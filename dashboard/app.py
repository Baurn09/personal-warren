"""Personal Warren dashboard — data pipeline + scoring.

Run with:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# make the `src` package importable when launched via `streamlit run`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json                  # noqa: E402

import pandas as pd          # noqa: E402
import streamlit as st       # noqa: E402

from src.company import report as company_report   # noqa: E402
from src.company.resolve import resolve_name       # noqa: E402
from src.config import load_config, resolve   # noqa: E402
from src.db.schema import connect             # noqa: E402

st.set_page_config(page_title="Personal Warren", layout="wide")

cfg = load_config()
db_path = resolve(cfg["paths"]["database"])

st.title("Personal Warren")

if not Path(db_path).exists():
    st.warning("No database yet. Run:  python run_pipeline.py")
    st.stop()


def _table_has_rows(conn, table: str) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    if not exists:
        return False
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0


(tab_data, tab_company, tab_monthly, tab_backtest, tab_learn,
 tab_score, tab_advisor, tab_ai) = st.tabs(
    ["Data Pipeline", "Company Analyzer", "Monthly Picks", "Backtest",
     "Learning Log", "Scoring & Ranking", "Advisor (legacy)",
     "AI Theses (legacy)"])

# ─────────────────────────── Data Pipeline ───────────────────────────
with tab_data:
    with connect(db_path) as conn:
        companies = pd.read_sql_query("SELECT * FROM companies", conn)
        n_prices = pd.read_sql_query(
            "SELECT COUNT(DISTINCT ticker) AS c FROM prices", conn)["c"][0]
        n_fund = pd.read_sql_query(
            "SELECT COUNT(DISTINCT ticker) AS c FROM fundamentals", conn)["c"][0]
        n_news = pd.read_sql_query("SELECT COUNT(*) AS c FROM news", conn)["c"][0]
        dq = pd.read_sql_query("SELECT * FROM data_quality", conn)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Universe", len(companies))
    c2.metric("With prices", int(n_prices))
    c3.metric("With fundamentals", int(n_fund))
    c4.metric("News items", int(n_news))

    st.subheader("Data-quality gate — latest run")
    if dq.empty:
        st.info("No quality checks recorded yet. Run:  python run_pipeline.py")
    else:
        latest = dq["run_at"].max()
        run = dq[dq["run_at"] == latest]
        overall = run[run["check_name"] == "overall"]
        g1, g2, g3 = st.columns(3)
        g1.metric("Pass", int((overall["status"] == "pass").sum()))
        g2.metric("Warn", int((overall["status"] == "warn").sum()))
        g3.metric("Fail", int((overall["status"] == "fail").sum()))
        st.caption(f"Run at {latest}")

        only_problems = st.checkbox("Show only warn / fail", value=True)
        detail = run[run["check_name"] != "overall"]
        if only_problems:
            detail = detail[detail["status"] != "pass"]
        st.dataframe(detail[["ticker", "check_name", "status", "detail"]],
                     use_container_width=True, hide_index=True)

# ─────────────────────────── Company Analyzer ───────────────────────
with tab_company:
    st.subheader("Analyse any Indian listed company")
    st.caption("Type a company name (or a symbol) → expected return over "
               "1m / 6m / 12m / 5y as Bear/Base/Bull scenarios, swing-vs-hold, "
               "quality vs sector peers, news and an AI read. Numbers are "
               "computed in Python; estimates are scenarios, not predictions.")

    q_col, opt_col = st.columns([3, 2])
    query = q_col.text_input("Company name or symbol", key="ca_query",
                             placeholder="e.g. Reliance, Tata Motors, INFY")
    as_symbol = opt_col.checkbox("Input is a symbol", key="ca_symbol")
    deep = opt_col.checkbox("Deep AI debate (Bull/Bear/Value/Growth)", key="ca_deep")
    no_ai = opt_col.checkbox("Skip AI (no API key needed)", value=True, key="ca_noai")
    refresh = opt_col.checkbox("Re-fetch data", key="ca_refresh")

    chosen_ticker = None
    if as_symbol:
        chosen_ticker = (query or "").upper().strip() or None
    else:
        if st.button("Search by name", key="ca_search") and query.strip():
            try:
                st.session_state["ca_candidates"] = resolve_name(query)["candidates"]
            except Exception as e:                             # noqa: BLE001
                st.error(f"Search failed: {e}")
                st.session_state["ca_candidates"] = []
        cands = st.session_state.get("ca_candidates", [])
        if cands:
            labels = [f"{c['name']}  [{c['ticker']}]  ({c['symbol']})"
                      for c in cands]
            pick = st.selectbox("Pick the company", labels, key="ca_pick")
            chosen_ticker = cands[labels.index(pick)]["ticker"]

    if st.button("Analyze", key="ca_run", disabled=not chosen_ticker):
        with st.spinner(f"Analysing {chosen_ticker} — fetching data, peers, "
                        "news, estimates ..."):
            try:
                company_report.analyze_company(
                    chosen_ticker, refresh=refresh,
                    ai_mode="deep" if deep else "lite", no_ai=no_ai)
                st.session_state["ca_selected"] = chosen_ticker
                st.success(f"Analysed {chosen_ticker}.")
            except Exception as e:                             # noqa: BLE001
                st.error(f"Analysis failed: {e}")

    with connect(db_path) as conn:
        has_ca = _table_has_rows(conn, "company_analysis")
        analyses = pd.read_sql_query(
            "SELECT * FROM company_analysis ORDER BY created_at DESC, ticker",
            conn) if has_ca else pd.DataFrame()

    st.divider()
    if analyses.empty:
        st.info("No company analysed yet. Search above, or run:  "
                'python run_company.py "Reliance"')
    else:
        opts = [f"{r['name']}  [{r['ticker']}]  ·  {r['run_date']}"
                for _, r in analyses.iterrows()]
        sel_ticker = st.session_state.get("ca_selected")
        idx = 0
        if sel_ticker in analyses["ticker"].tolist():
            idx = analyses["ticker"].tolist().index(sel_ticker)
        choice = st.selectbox("View analysis", opts, index=idx, key="ca_view")
        a = analyses.iloc[opts.index(choice)]

        with connect(db_path) as conn:
            est = pd.read_sql_query(
                "SELECT * FROM company_estimates WHERE ticker=? AND run_date=? "
                "ORDER BY days", conn, params=(a["ticker"], a["run_date"]))
            news_df = pd.read_sql_query(
                "SELECT title, source, url, published FROM news WHERE ticker=? "
                "ORDER BY fetched_at DESC LIMIT 8", conn, params=(a["ticker"],))

        st.markdown(f"### {a['name']}  ·  {a['sector'] or '—'}")
        h1, h2, h3, h4 = st.columns(4)
        if pd.notna(a["current_price"]):
            h1.metric("Price", f"Rs {a['current_price']:,.0f}")
        h2.metric("Quality composite", f"{a['quality_composite']:.0f}/100")
        sp = "—" if pd.isna(a["sector_percentile"]) else f"{a['sector_percentile']:.0f}th"
        h3.metric("Vs peers", sp, help=f"{a['quality_mode']} scoring")
        h4.metric("Confidence", str(a["confidence"]).title())

        # quality categories
        try:
            cats = json.loads(a["categories_json"]) if a["categories_json"] else {}
        except (TypeError, ValueError):
            cats = {}
        if cats:
            order = ["quality", "moat", "financial_strength", "management",
                     "valuation", "growth"]
            cat_df = pd.DataFrame(
                {"category": [c.replace("_", " ").title() for c in order],
                 "score": [cats.get(c) for c in order]})
            st.caption("Quality / MOAT breakdown (0-100)")
            st.bar_chart(cat_df.set_index("category"), height=200)
        flags = {}
        try:
            flags = json.loads(a["flags_json"]) if a["flags_json"] else {}
        except (TypeError, ValueError):
            flags = {}
        all_flags = (flags.get("disqualifiers") or []) + (flags.get("penalties") or [])
        if all_flags:
            st.warning("Red flags: " + ", ".join(all_flags))

        # estimates
        st.subheader("Expected return by horizon")
        if est.empty:
            st.info("No estimates stored (insufficient price history).")
        else:
            notional = float(cfg["company_analysis"]["notional_rupees"])
            show = est.copy()
            label_map = {"1m": "1 month", "6m": "6 months",
                         "12m": "12 months", "5y": "5 years"}
            show["horizon"] = show["horizon"].map(label_map).fillna(show["horizon"])
            for c in ("bear", "base", "bull", "prob_positive", "nifty_alpha"):
                show[c] = show[c] * 100
            show[f"Rs on {notional:,.0f}"] = (est["base"] * notional).round(0)
            st.dataframe(
                show[["horizon", "bear", "base", "bull", "prob_positive",
                      "nifty_alpha", f"Rs on {notional:,.0f}", "confidence"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "horizon": "horizon",
                    "bear": st.column_config.NumberColumn("bear %", format="%+.1f"),
                    "base": st.column_config.NumberColumn("base %", format="%+.1f"),
                    "bull": st.column_config.NumberColumn("bull %", format="%+.1f"),
                    "prob_positive": st.column_config.NumberColumn(
                        "P(gain) %", format="%.0f"),
                    "nifty_alpha": st.column_config.NumberColumn(
                        "vs Nifty %", format="%+.1f"),
                })
            chart = est.copy()
            chart["horizon"] = chart["horizon"].map(label_map).fillna(chart["horizon"])
            st.line_chart(
                chart.set_index("horizon")[["bear", "base", "bull"]] * 100,
                height=240)

        # swing vs hold
        st.subheader("1-month: swing trade vs buy-and-hold")
        s1, s2, s3 = st.columns(3)
        s1.metric("Swing EV",
                  "—" if pd.isna(a["swing_ev_pct"]) else f"{a['swing_ev_pct']:+.1f}%")
        s2.metric("Hold-1m EV",
                  "—" if pd.isna(a["hold_ev_pct"]) else f"{a['hold_ev_pct']:+.1f}%")
        s3.metric("Suggestion", str(a["swing_reco"]))
        st.caption(
            f"Plan — entry ~Rs {a['entry_price']:,.0f} · "
            f"stop Rs {a['stop_loss']:,.0f} "
            f"({(a['stop_loss'] / a['entry_price'] - 1) * 100:+.1f}%) · "
            f"target Rs {a['target_price']:,.0f} "
            f"({(a['target_price'] / a['entry_price'] - 1) * 100:+.1f}%)")

        # news
        if not news_df.empty:
            st.subheader("Recent news")
            for _, n in news_df.iterrows():
                if n["url"]:
                    st.markdown(f"- [{n['title']}]({n['url']})  ·  _{n['source']}_")
                else:
                    st.markdown(f"- {n['title']}  ·  _{n['source']}_")

        # AI narrative
        if isinstance(a["ai_narrative"], str) and a["ai_narrative"].strip():
            st.subheader(f"AI analyst ({a['ai_mode']})")
            if isinstance(a["ai_verdict"], str) and a["ai_verdict"]:
                st.markdown(f"**Verdict: {a['ai_verdict']}**  "
                            f"(confidence {a['ai_confidence']})")
            st.markdown(a["ai_narrative"])
        else:
            st.caption("No AI narrative. Re-run with AI enabled (uncheck "
                       "'Skip AI') and an OPENROUTER_API_KEY set.")

        st.caption("Estimates are probabilistic scenarios from historical "
                   "behaviour + fundamentals, NOT predictions. Personal research "
                   "aid, not SEBI-registered advice.")

# ─────────────────────────── Monthly Picks ──────────────────────────
with tab_monthly:
    with connect(db_path) as conn:
        has_picks = _table_has_rows(conn, "monthly_advice")
        if has_picks:
            mr = pd.read_sql_query(
                "SELECT * FROM monthly_rankings "
                "WHERE run_date=(SELECT MAX(run_date) FROM monthly_rankings)",
                conn)
            ma = pd.read_sql_query(
                "SELECT * FROM monthly_advice "
                "WHERE run_date=(SELECT MAX(run_date) FROM monthly_advice)",
                conn)
            ms = pd.read_sql_query(
                "SELECT ticker, mom_12_1, rsi_14, atr_14, vol_1m, current_price "
                "FROM momentum_signals "
                "WHERE run_date=(SELECT MAX(run_date) FROM momentum_signals)",
                conn)
            mc = pd.read_sql_query("SELECT ticker, name, sector FROM companies",
                                   conn)
            if _table_has_rows(conn, "monthly_briefs"):
                mb = pd.read_sql_query(
                    "SELECT ticker, brief FROM monthly_briefs "
                    "WHERE run_date=(SELECT MAX(run_date) FROM monthly_briefs)",
                    conn)
            else:
                mb = pd.DataFrame(columns=["ticker", "brief"])

    if not has_picks:
        st.info("No monthly picks yet. Run:  python run_scoring.py")
    else:
        n_universe = len(mr)
        n_quality = int((mr["quality_passed"] == 1).sum())
        n_eligible = int(
            ((mr["quality_passed"] == 1) & (mr["trend_passed"] == 1)
             & (mr["rank"].notna())).sum())
        n_shortlist = int((mr["in_shortlist"] == 1).sum())
        run_date = mr["run_date"].max()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Universe scored", n_universe)
        c2.metric("Quality passed", n_quality)
        c3.metric("Eligible", n_eligible)
        c4.metric("Shortlist", n_shortlist)
        st.caption(f"Run date {run_date} · ranked by sector-relative momentum "
                   "(12-1, 6m return, 52-week-high distance, volume trend) "
                   "inside the quality filter.")

        picks = (mr[mr["in_shortlist"] == 1]
                 .merge(ma, on=["ticker", "run_date"], how="left")
                 .merge(ms, on="ticker", how="left")
                 .merge(mc, on="ticker", how="left")
                 .merge(mb, on="ticker", how="left")
                 .sort_values("rank"))
        picks["mom_12_1_pct"] = (picks["mom_12_1"] * 100).round(1)

        st.subheader("Shortlist")
        cols = ["rank", "ticker", "name", "sector", "quality_score",
                "momentum_score", "mom_12_1_pct", "rsi_14",
                "entry_price", "stop_loss", "target_price",
                "position_size_pct"]
        st.dataframe(
            picks[cols], use_container_width=True, hide_index=True,
            column_config={
                "quality_score": st.column_config.NumberColumn(
                    "quality", format="%.0f"),
                "momentum_score": st.column_config.NumberColumn(
                    "momentum", format="%.1f"),
                "mom_12_1_pct": st.column_config.NumberColumn(
                    "12-1 ret %", format="%.1f"),
                "rsi_14": st.column_config.NumberColumn(
                    "RSI", format="%.0f"),
                "entry_price": st.column_config.NumberColumn(
                    "entry", format="%.0f"),
                "stop_loss": st.column_config.NumberColumn(
                    "stop", format="%.0f"),
                "target_price": st.column_config.NumberColumn(
                    "target", format="%.0f"),
                "position_size_pct": st.column_config.NumberColumn(
                    "size %", format="%.1f"),
            })
        total_pos = float(picks["position_size_pct"].sum())
        st.caption(f"Total allocated: {total_pos:.1f}% · "
                   f"cash: {100 - total_pos:.1f}%")

        st.subheader("Advisor's plan per pick")
        pick_choice = st.selectbox(
            "Pick a stock", picks["ticker"].tolist())
        row = picks[picks["ticker"] == pick_choice].iloc[0]
        st.markdown(f"**{row['ticker']} — {row['name']}**  ·  rank "
                    f"{int(row['rank'])} · {row['sector'] or '—'}")
        st.write(row["rationale"])

        brief_text = row.get("brief") if "brief" in row.index else None
        if isinstance(brief_text, str) and brief_text.strip():
            st.markdown("**Monthly brief (AI):**")
            st.write(brief_text)
        else:
            st.caption("No AI brief yet for this pick. Run:  python run_briefs.py")

# ─────────────────────────── Backtest ────────────────────────────────
with tab_backtest:
    with connect(db_path) as conn:
        has_bt = _table_has_rows(conn, "backtest_runs")
        if has_bt:
            runs = pd.read_sql_query(
                "SELECT * FROM backtest_runs ORDER BY id DESC", conn)
        else:
            runs = pd.DataFrame()

    if runs.empty:
        st.info("No backtest yet. Run:  python run_backtest.py")
    else:
        run_options = [
            f"#{r.id}  ·  {r.start_date} → {r.end_date}  "
            f"({r.months_total} months)"
            for r in runs.itertuples()
        ]
        choice = st.selectbox("Backtest run", run_options, index=0)
        run_id = int(runs.iloc[run_options.index(choice)]["id"])
        run_row = runs[runs["id"] == run_id].iloc[0]

        with connect(db_path) as conn:
            monthly = pd.read_sql_query(
                "SELECT * FROM backtest_monthly WHERE run_id=? "
                "ORDER BY month", conn, params=(run_id,))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Months total", int(run_row["months_total"]))
        m2.metric("Beat Nifty",
                  f"{int(run_row['months_beating_nifty'])} / "
                  f"{int(run_row['months_total'])}",
                  delta=f"{run_row['months_beating_nifty'] / run_row['months_total'] * 100:.0f}% hit-rate")
        m3.metric("Total return", f"{run_row['total_return_pct']:.1f}%",
                  delta=f"{run_row['total_return_pct'] - run_row['nifty_return_pct']:+.1f}% vs Nifty TRI")
        m4.metric("Max drawdown", f"{run_row['max_drawdown_pct']:.1f}%")

        st.caption(
            f"Avg excess return / month: "
            f"**{run_row['avg_excess_pct']:+.3f}%**   ·   "
            f"Nifty TRI total: {run_row['nifty_return_pct']:.1f}%   ·   "
            f"Config hash: `{run_row['config_hash']}`")

        st.warning(
            "Look-ahead bias caveat: the quality filter uses the *current* "
            "fundamentals snapshot at every backtest month (we don't yet store "
            "point-in-time fundamentals). Treat the hit rate as an upper bound, "
            "not a forecast. The momentum signals themselves are point-in-time.")

        # ─── equity curves ───
        if not monthly.empty:
            curve = monthly.copy()
            curve["portfolio_eq"] = (1.0 + curve["portfolio_return"]).cumprod()
            curve["nifty_eq"] = (1.0 + curve["nifty_return"]).cumprod()
            curve_chart = curve.set_index("month")[
                ["portfolio_eq", "nifty_eq"]
            ].rename(columns={"portfolio_eq": "Quality-Momentum",
                              "nifty_eq": "Nifty 50 TRI"})
            st.subheader("Equity curve (₹1 invested at start)")
            st.line_chart(curve_chart, height=320)

            # ─── monthly excess return ───
            st.subheader("Monthly excess return vs Nifty 50")
            bar = curve.set_index("month")[["excess_return"]] * 100.0
            bar = bar.rename(columns={"excess_return": "excess % (port − Nifty)"})
            st.bar_chart(bar, height=240)

            # ─── per-month table ───
            with st.expander("Per-month details"):
                show = curve[["month", "portfolio_return", "nifty_return",
                              "excess_return", "n_holdings"]].copy()
                for c in ("portfolio_return", "nifty_return", "excess_return"):
                    show[c] = (show[c] * 100).round(2)
                st.dataframe(
                    show, use_container_width=True, hide_index=True,
                    column_config={
                        "portfolio_return": st.column_config.NumberColumn(
                            "port %", format="%.2f"),
                        "nifty_return": st.column_config.NumberColumn(
                            "nifty %", format="%.2f"),
                        "excess_return": st.column_config.NumberColumn(
                            "excess %", format="%.2f"),
                    })


# ─────────────────────────── Learning Log ───────────────────────────
with tab_learn:
    with connect(db_path) as conn:
        has_log = _table_has_rows(conn, "pick_outcomes")
        if has_log:
            outcomes = pd.read_sql_query(
                "SELECT * FROM pick_outcomes "
                "ORDER BY run_date DESC, ticker", conn)
            names_l = pd.read_sql_query(
                "SELECT ticker, name FROM companies", conn)

    if not has_log:
        st.info("No reviewed picks yet. Run:  python run_review.py")
    else:
        merged = outcomes.merge(names_l, on="ticker", how="left")
        closed = merged[merged["outcome"] != "not_yet"]
        open_picks = merged[merged["outcome"] == "not_yet"]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Closed", len(closed))
        c2.metric("Open (in window)", len(open_picks))
        if not closed.empty:
            winners = int((closed["actual_return_pct"] > 0).sum())
            c3.metric("Winners",
                      f"{winners} / {len(closed)}",
                      delta=f"{winners / len(closed) * 100:.0f}% win-rate")
            beat = int((closed["excess_return_pct"] > 0).sum())
            c4.metric("Beat Nifty",
                      f"{beat} / {len(closed)}",
                      delta=f"{beat / len(closed) * 100:.0f}% hit-rate")
        else:
            c3.metric("Winners", "—")
            c4.metric("Beat Nifty", "—")

        if not closed.empty:
            avg_ret = closed["actual_return_pct"].mean()
            avg_ex = closed["excess_return_pct"].dropna().mean()
            st.caption(
                f"Avg return per closed pick: **{avg_ret:+.2f}%**   ·   "
                f"Avg excess vs Nifty: **{avg_ex:+.2f}%**.   "
                f"`stop_hit` = stop was breached; `target_hit` = target met; "
                f"`held` = neither, marked-to-market at the close of the "
                f"holding window.")

        st.subheader("Outcomes")
        cols = ["run_date", "ticker", "name", "outcome",
                "entry_price", "exit_price", "actual_return_pct",
                "nifty_return_pct", "excess_return_pct",
                "exit_date"]
        st.dataframe(
            merged[cols], use_container_width=True, hide_index=True,
            column_config={
                "entry_price": st.column_config.NumberColumn(
                    "entry", format="%.0f"),
                "exit_price": st.column_config.NumberColumn(
                    "exit", format="%.0f"),
                "actual_return_pct": st.column_config.NumberColumn(
                    "return %", format="%+.2f"),
                "nifty_return_pct": st.column_config.NumberColumn(
                    "nifty %", format="%+.2f"),
                "excess_return_pct": st.column_config.NumberColumn(
                    "excess %", format="%+.2f"),
            })

        if not closed.empty:
            st.subheader("Outcome mix")
            mix = closed["outcome"].value_counts()
            st.bar_chart(mix)


# ─────────────────────────── Scoring & Ranking ───────────────────────
with tab_score:
    with connect(db_path) as conn:
        has_run = _table_has_rows(conn, "rankings")
        if has_run:
            rankings = pd.read_sql_query(
                "SELECT * FROM rankings "
                "WHERE run_date=(SELECT MAX(run_date) FROM rankings)", conn)
            scores = pd.read_sql_query(
                "SELECT * FROM scores "
                "WHERE run_date=(SELECT MAX(run_date) FROM scores)", conn)
            flags_df = pd.read_sql_query(
                "SELECT * FROM flags "
                "WHERE run_date=(SELECT MAX(run_date) FROM flags)", conn)
            names = pd.read_sql_query(
                "SELECT ticker, name, sector FROM companies", conn)

    if not has_run:
        st.info("No scoring run yet. Run:  python run_scoring.py")
    else:
        cats = ["quality", "moat", "financial_strength",
                "management", "valuation", "growth"]
        cat_wide = scores.pivot_table(index="ticker", columns="category",
                                      values="score").reset_index()
        tbl = (rankings.merge(names, on="ticker", how="left")
                       .merge(cat_wide, on="ticker", how="left")
                       .sort_values("rank"))
        n_disq = flags_df[flags_df["severity"] == "disqualify"]["ticker"].nunique()
        n_pen = flags_df[flags_df["severity"] == "penalty"]["ticker"].nunique()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scored", len(rankings))
        m2.metric("Shortlist", int(rankings["in_shortlist"].sum()))
        m3.metric("Disqualified", int(n_disq))
        m4.metric("Penalised", int(n_pen))
        st.caption(f"Run date {rankings['run_date'].iloc[0]}  |  "
                   "category scores are 0-100 sector-relative percentiles")

        shortlist_only = st.checkbox("Show shortlist only", value=False)
        view = tbl[tbl["in_shortlist"] == 1] if shortlist_only else tbl
        cols = (["rank", "ticker", "name", "sector", "template"]
                + cats + ["total_score"])
        cols = [c for c in cols if c in view.columns]
        st.dataframe(view[cols], use_container_width=True, hide_index=True)

        with st.expander("Red flags this run"):
            if flags_df.empty:
                st.write("None.")
            else:
                st.dataframe(
                    flags_df[["ticker", "flag", "severity", "detail"]],
                    use_container_width=True, hide_index=True)

# ─────────────────────────── Advisor ─────────────────────────────────
with tab_advisor:
    with connect(db_path) as conn:
        has_adv = _table_has_rows(conn, "valuation_advice")
        if has_adv:
            adv = pd.read_sql_query(
                "SELECT * FROM valuation_advice "
                "WHERE run_date=(SELECT MAX(run_date) FROM valuation_advice)",
                conn)
            adv_names = pd.read_sql_query(
                "SELECT ticker, name FROM companies", conn)

    if not has_adv:
        st.info("No valuation advice yet. Run:  python run_scoring.py")
    else:
        adv = adv.merge(adv_names, on="ticker", how="left")
        adv["mos_pct"] = (adv["margin_of_safety"] * 100).round(1)
        counts = adv["action"].value_counts()

        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Buy Now", int(counts.get("Buy Now", 0)))
        b2.metric("Accumulate", int(counts.get("Accumulate Gradually", 0)))
        b3.metric("Wait", int(counts.get("Wait", 0)))
        b4.metric("No call", int(counts.get("No call", 0)))
        st.caption("Fair value is a deterministic margin-of-safety estimate "
                   "(fair P/E x EPS). A positive margin of safety means the "
                   "price is below estimated fair value.")

        choice = st.selectbox("Filter by recommendation",
                              ["All", "Buy Now", "Accumulate Gradually", "Wait"])
        view = adv if choice == "All" else adv[adv["action"] == choice]
        view = view.sort_values("margin_of_safety", ascending=False,
                                na_position="last")
        st.dataframe(
            view[["ticker", "name", "current_price", "fair_value", "mos_pct",
                  "valuation_verdict", "action", "target_buy_price"]],
            use_container_width=True, hide_index=True,
            column_config={
                "mos_pct": st.column_config.NumberColumn(
                    "margin of safety %", format="%.1f"),
                "current_price": st.column_config.NumberColumn(
                    "price", format="%.0f"),
                "fair_value": st.column_config.NumberColumn(
                    "fair value", format="%.0f"),
                "target_buy_price": st.column_config.NumberColumn(
                    "buy below", format="%.0f"),
            })

        st.subheader("Advisor's take")
        pick = st.selectbox(
            "Pick a stock", sorted(adv["ticker"].tolist()))
        row = adv[adv["ticker"] == pick].iloc[0]
        st.markdown(f"**{row['ticker']} — {row['name']}**   ·   "
                    f"{row['action']}  ({row['valuation_verdict']})")
        st.write(row["rationale"])

# ─────────────────────────── AI Theses ───────────────────────────────
with tab_ai:
    with connect(db_path) as conn:
        has_theses = _table_has_rows(conn, "theses")
        if has_theses:
            theses = pd.read_sql_query(
                "SELECT * FROM theses "
                "WHERE run_date=(SELECT MAX(run_date) FROM theses)", conn)
            names = pd.read_sql_query("SELECT ticker, name FROM companies", conn)
            if _table_has_rows(conn, "valuation_advice"):
                price_calls = pd.read_sql_query(
                    "SELECT ticker, action AS price_call FROM valuation_advice "
                    "WHERE run_date=(SELECT MAX(run_date) FROM valuation_advice)",
                    conn)
            else:
                price_calls = pd.DataFrame(columns=["ticker", "price_call"])

    if not has_theses:
        st.info("No AI theses yet. Run:  python run_agents.py")
    else:
        theses = (theses.merge(names, on="ticker", how="left")
                        .merge(price_calls, on="ticker", how="left")
                        .sort_values("total_score", ascending=False))
        verdicts = theses["verdict"].value_counts()
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Theses", len(theses))
        a2.metric("Strong Buy", int(verdicts.get("Strong Buy", 0)))
        a3.metric("Watchlist", int(verdicts.get("Watchlist", 0)))
        a4.metric("Avoid", int(verdicts.get("Avoid", 0)))
        st.caption(f"Run date {theses['run_date'].iloc[0]}")

        st.caption("`verdict` = the AI's view of the business; "
                   "`price_call` = the advisor's view of the price today.")
        st.dataframe(
            theses[["ticker", "name", "verdict", "confidence",
                    "price_call", "total_score"]],
            use_container_width=True, hide_index=True)

        st.subheader("Full theses")
        for _, row in theses.iterrows():
            with st.expander(f"{row['ticker']} — {row['name']}   ·   "
                             f"{row['verdict']} (confidence {row['confidence']})"):
                st.markdown(row["thesis"] or "_no thesis_")
                st.markdown("---")
                left, right = st.columns(2)
                left.markdown("**Bull**\n\n" + (row["bull"] or "_n/a_"))
                left.markdown("**Value**\n\n" + (row["value_view"] or "_n/a_"))
                right.markdown("**Bear**\n\n" + (row["bear"] or "_n/a_"))
                right.markdown("**Growth**\n\n" + (row["growth_view"] or "_n/a_"))
