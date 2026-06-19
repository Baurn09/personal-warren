"""Render the deterministic single-company dossier handed to the AI analyst.

Every number here is computed in Python and formatted verbatim. The analyst
reasons from these facts and never invents figures — the AI / logic separation
from AGENTS.md, enforced at the prompt boundary.
"""
from __future__ import annotations

from typing import Optional

_CRORE = {"revenue", "net_profit", "market_cap", "fcf", "total_debt",
          "operating_cashflow", "ebitda"}
_PERCENT = {"roe", "roa", "net_margin", "operating_margin", "gross_margin",
            "revenue_growth", "earnings_growth", "promoter_holding",
            "payout_ratio", "dividend_yield"}


def _fmt(metric: str, v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "n/a"
    if metric in _CRORE:
        return f"Rs {v / 1e7:,.0f} cr"
    if metric in _PERCENT:
        return f"{v * 100:.1f}%"
    if metric == "debt_to_equity":
        return f"{v / 100:.2f}x"
    return f"{v:.2f}"


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:+.1f}%"


def build_company_dossier(report: dict) -> str:
    """Render the report dict as the analyst dossier (plain text)."""
    p = report["profile"]
    q = report["quality"]
    m = p["metrics"]
    est = report["estimates"]
    sw = report["swing"]

    def metric(k: str) -> str:
        return _fmt(k, m.get(k))

    summary = (p.get("business_summary") or "").strip()
    if len(summary) > 700:
        summary = summary[:700].rsplit(" ", 1)[0] + " ..."

    cats = q["categories"]
    sect_pct = ("n/a" if q["sector_percentile"] is None
                else f"{q['sector_percentile']:.0f}th pct of sampled peers")
    flags_line = ", ".join(q["disqualifiers"] + q["penalties"]) or "none"

    # scenario table
    lines = []
    for h in ("1m", "6m", "12m", "5y"):
        e = est.get(h)
        if not e:
            lines.append(f"  {h:>3}: insufficient history")
            continue
        alpha = "" if e["nifty_alpha"] is None else \
            f"   vs Nifty {_pct(e['nifty_alpha'])}"
        lines.append(
            f"  {h:>3}: base {_pct(e['base'])}  "
            f"(bear {_pct(e['bear'])} / bull {_pct(e['bull'])})  "
            f"P(gain) {e['prob_positive'] * 100:.0f}%  [{e['confidence']}]{alpha}")
    scenario_block = "\n".join(lines)

    plan = report["plan"]
    sw_ev = "n/a" if sw["swing_ev"] is None else _pct(sw["swing_ev"])
    hold_ev = "n/a" if sw["hold_ev"] is None else _pct(sw["hold_ev"])
    s = sw.get("swing")
    swing_detail = ""
    if s:
        swing_detail = (f" (P target {s['p_target'] * 100:.0f}% / "
                        f"stop {s['p_stop'] * 100:.0f}% / "
                        f"neither {s['p_neither'] * 100:.0f}%)")

    news = report.get("news") or []
    if news:
        news_block = "\n".join(f"  - {n['title']} ({n['source']})"
                               for n in news[:6] if n.get("title"))
    else:
        news_block = "  (no recent company-specific headlines retrieved)"

    return f"""COMPANY: {p['name']}  [{p['ticker']}]
Sector: {p.get('sector') or 'n/a'}   |   Scoring template: {p['template']}

BUSINESS
{summary or '  (no business summary available)'}

QUALITY / MOAT  (0-100; {q['mode']} scoring) — {sect_pct}
  Quality {cats['quality']:.0f}  Moat {cats['moat']:.0f}  \
Financial-Strength {cats['financial_strength']:.0f}  \
Management {cats['management']:.0f}  Valuation {cats['valuation']:.0f}  \
Growth {cats['growth']:.0f}
  Composite (Quality+Moat+FS) {q['composite']:.0f} / 100   \
Total after penalties {q['final_total']:.0f} / 100
  Red flags: {flags_line}

KEY METRICS (trailing twelve months)
  Profitability : ROE {metric('roe')}  ROA {metric('roa')}  \
Net margin {metric('net_margin')}  Operating margin {metric('operating_margin')}
  Valuation     : PE {metric('pe')}  P/B {metric('price_to_book')}  \
EV/EBITDA {metric('ev_ebitda')}  Dividend yield {metric('dividend_yield')}
  Balance sheet : Debt/Equity {metric('debt_to_equity')}  FCF {metric('fcf')}
  Scale         : Revenue {metric('revenue')}  Net profit {metric('net_profit')}  \
Market cap {metric('market_cap')}
  Growth        : Revenue growth {metric('revenue_growth')}  \
Earnings growth {metric('earnings_growth')}

EXPECTED RETURN SCENARIOS (deterministic; bear/base/bull total return)
{scenario_block}

1-MONTH SWING vs HOLD
  Entry ~Rs {plan['entry_price']:,.0f}   Stop Rs {plan['stop_loss']:,.0f}   \
Target Rs {plan['target_price']:,.0f}
  Swing EV {sw_ev}{swing_detail}   |   Hold-1-month EV {hold_ev}
  Suggestion: {sw['recommendation']}

RECENT NEWS HEADLINES
{news_block}"""
