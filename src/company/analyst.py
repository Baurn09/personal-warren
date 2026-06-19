"""AI qualitative layer for the Company Analyzer.

Two modes, both reusing the cached OpenRouter ``LLMClient``:

* **lite** (default) — one call producing a plain-prose read of business
  operations, MOAT and future prospects, plus the 1-month catalyst and key
  risks. Reasons only from the deterministic dossier; never invents numbers.
* **deep** — the dormant Bull/Bear/Value/Growth specialists + Judge debate,
  repurposed for a single company.

Degrades gracefully: with no API key or on a quota error the estimates still
stand and the narrative is simply absent.
"""
from __future__ import annotations

from typing import Optional

from src.agents.judge import run_judge
from src.agents.llm import LLMClient
from src.agents.specialists import run_specialist
from src.config import load_config, resolve

_SYSTEM_LITE = (
    "You are an equity research analyst writing for an Indian retail investor "
    "with a swing-trading and long-term (no day-trading) horizon. Reason ONLY "
    "from the dossier provided — never invent, estimate or recall financial "
    "figures or prices; if something is not in the dossier, treat it as unknown. "
    "Plain prose, ~180 words, no headings beyond the four labelled lines asked for."
)

_TASK_LITE = (
    "Write a concise read covering, each as its own short labelled paragraph:\n"
    "BUSINESS & MOAT: what the company does and how durable its competitive "
    "advantage looks, grounded in the margins/returns/quality scores.\n"
    "FUTURE PROSPECTS: the multi-year outlook implied by the growth and "
    "long-horizon scenario figures.\n"
    "1-MONTH VIEW: the most likely catalyst and the single biggest 30-day risk, "
    "tied to the swing-vs-hold suggestion.\n"
    "BOTTOM LINE: one sentence on whether this suits swing trading, long-term "
    "holding, both, or neither for a small investor."
)


def _client(use_cache: bool):
    cfg = load_config()
    db_path = resolve(cfg["paths"]["database"])
    return LLMClient(db_path, use_cache=use_cache), cfg["ai"]["models"]["specialist"]


def run_lite(dossier: str, use_cache: bool = True) -> dict:
    client, model = _client(use_cache)
    text = client.chat(_SYSTEM_LITE, f"{dossier}\n\nTASK: {_TASK_LITE}", model)
    return {"mode": "lite", "narrative": text, "model": model,
            "verdict": None, "confidence": None}


def run_deep(dossier: str, use_cache: bool = True) -> dict:
    client, model = _client(use_cache)
    views = {role: run_specialist(client, role, dossier, model)
             for role in ("bull", "bear", "value", "growth")}
    thesis, verdict, confidence = run_judge(client, dossier, views, model)
    narrative = thesis + "\n\n---\n" + "\n\n".join(
        f"**{r.upper()}**\n{t}" for r, t in views.items())
    return {"mode": "deep", "narrative": narrative, "model": model,
            "verdict": verdict, "confidence": confidence, "views": views,
            "thesis": thesis}


def analyze(dossier: str, mode: str = "lite", use_cache: bool = True,
            no_ai: bool = False) -> dict:
    """Run the AI layer. Returns a dict with ``narrative`` (or ``None``) + status."""
    if no_ai:
        return {"mode": "none", "narrative": None, "model": None,
                "verdict": None, "confidence": None, "status": "skipped"}
    try:
        result = run_deep(dossier, use_cache) if mode == "deep" \
            else run_lite(dossier, use_cache)
        result["status"] = "ok"
        return result
    except Exception as e:                                      # noqa: BLE001
        return {"mode": "none", "narrative": None, "model": None,
                "verdict": None, "confidence": None,
                "status": f"unavailable: {e}"}
