"""Bull / Bear / Value / Growth specialist agents.

Each takes the same deterministic dossier and argues its own angle. They run on
the funnel shortlist only — never the full universe.
"""
from __future__ import annotations

from src.agents.llm import LLMClient

_SHARED_RULES = (
    " Rules: reason ONLY from the data in the dossier. Never invent, estimate "
    "or recall financial figures from memory; if something is not in the "
    "dossier, treat it as unknown. Be specific and evidence-based. "
    "Max 180 words, plain prose, no headings."
)

# role -> (persona, task)
SPECIALISTS = {
    "bull": (
        "You are a Bull-case analyst in a long-term, Buffett-style research team.",
        "Make the strongest evidence-based case for why this business can "
        "compound and dominate its space in India over the next 5-10 years."),
    "bear": (
        "You are a Bear-case analyst in a long-term, Buffett-style research team.",
        "Identify what could weaken, disrupt or destroy this company, and the "
        "warning signs visible in the data. Be sceptical and concrete."),
    "value": (
        "You are a Value analyst focused on balance-sheet safety and price.",
        "Assess debt, cash flow, ROE and valuation. Is this a financially sound "
        "business, and is the current valuation sensible for a long-term buyer?"),
    "growth": (
        "You are a Growth analyst.",
        "Assess revenue and earnings growth, reinvestment runway and expansion "
        "potential. Is there a long, durable growth runway?"),
}


def run_specialist(client: LLMClient, role: str, dossier: str, model: str) -> str:
    """Run one specialist agent and return its analysis text."""
    persona, task = SPECIALISTS[role]
    system = persona + _SHARED_RULES
    user = f"{dossier}\n\nTASK: {task}"
    return client.chat(system, user, model)
