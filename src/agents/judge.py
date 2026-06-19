"""The Judge agent — synthesises the specialist debate into a verdict."""
from __future__ import annotations

import re

from src.agents.llm import LLMClient

_JUDGE_SYSTEM = (
    "You are the Judge in a long-term, Buffett-style equity research team. You "
    "weigh the Bull, Bear, Value and Growth analyses against the deterministic "
    "score and produce one balanced verdict. Reason ONLY from the material "
    "provided; never invent figures. Be decisive but honest about uncertainty."
)

_JUDGE_TASK = """Write a concise investment thesis in EXACTLY this markdown format:

### Strengths
- (3-4 bullets)
### Weaknesses
- (3-4 bullets)
### Risks
- (2-3 bullets)
### Reasoning
(2-4 sentences weighing the debate against the deterministic score.)

Then, on their own two lines, end with EXACTLY:
VERDICT: <Strong Buy | Watchlist | Avoid>
CONFIDENCE: <integer 0-100>"""


def _parse_verdict(text: str) -> str:
    m = re.search(r"VERDICT:\s*(Strong Buy|Watchlist|Avoid)", text, re.IGNORECASE)
    return m.group(1).title() if m else "Watchlist"


def _parse_confidence(text: str) -> int:
    m = re.search(r"CONFIDENCE:\s*(\d{1,3})", text)
    return max(0, min(100, int(m.group(1)))) if m else 50


def run_judge(client: LLMClient, dossier: str, specialists: dict,
              model: str) -> tuple[str, str, int]:
    """Return ``(thesis_markdown, verdict, confidence)``."""
    debate = "\n\n".join(
        f"{role.upper()} ANALYST:\n{text}"
        for role, text in specialists.items())
    user = f"{dossier}\n\nSPECIALIST DEBATE:\n{debate}\n\n{_JUDGE_TASK}"
    thesis = client.chat(_JUDGE_SYSTEM, user, model)
    return thesis, _parse_verdict(thesis), _parse_confidence(thesis)
