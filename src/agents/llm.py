"""OpenRouter LLM client — chat completions with caching and a token budget.

Caching matters: re-running the agents should be free. Every response is keyed
by a hash of (model, messages) and stored in the ``ai_cache`` table, so an
unchanged prompt never costs a second API call.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests

from src.config import ROOT, load_config
from src.db.schema import connect


def load_api_key() -> str:
    """Read OPENROUTER_API_KEY from the environment or a project ``.env`` file."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key or key == "sk-or-v1-your-key-here":
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. Copy .env.example to .env and add your "
            "key from https://openrouter.ai/keys")
    return key


def _cache_key(model: str, messages: list[dict]) -> str:
    blob = json.dumps([model, messages], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLMClient:
    """Thin OpenRouter client. Tracks token usage and caches responses."""

    def __init__(self, db_path, use_cache: bool = True):
        self.cfg = load_config()["ai"]
        self.db_path = db_path
        self.use_cache = use_cache
        self.api_key = load_api_key()
        self.tokens_used = 0
        self.api_calls = 0
        self.cache_hits = 0

    # --- cache ---
    def _cache_get(self, key: str) -> str | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT response FROM ai_cache WHERE cache_key=?", (key,)).fetchone()
        return row[0] if row else None

    def _cache_put(self, key: str, model: str, response: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_cache "
                "(cache_key, model, response, created_at) VALUES (?,?,?,?)",
                (key, model, response, datetime.now(timezone.utc).isoformat()))

    # --- public API ---
    def chat(self, system: str, user: str, model: str) -> str:
        """Run one chat completion. Returns the assistant message content."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        key = _cache_key(model, messages)
        if self.use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                self.cache_hits += 1
                return cached

        budget = self.cfg["token_budget_per_run"]
        if self.tokens_used >= budget:
            raise RuntimeError(f"token budget ({budget}) exhausted for this run")

        content, tokens = self._call(model, messages)
        self.tokens_used += tokens
        self.api_calls += 1
        self._cache_put(key, model, content)
        time.sleep(self.cfg.get("delay_between_calls_seconds", 0))
        return content

    # --- http ---
    def _call(self, model: str, messages: list[dict]) -> tuple[str, int]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/personal-warren",
            "X-Title": "Personal Warren",
        }
        body = {
            "model": model,
            "messages": messages,
            "temperature": self.cfg["temperature"],
            "max_tokens": self.cfg["max_tokens"],
        }
        last_err = "unknown error"
        for attempt in range(self.cfg["max_retries"]):
            try:
                resp = requests.post(
                    self.cfg["base_url"], headers=headers, json=body,
                    timeout=self.cfg["request_timeout_seconds"])
            except requests.RequestException as e:
                last_err = str(e)
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    choice = data.get("choices", [{}])[0]
                    content = (choice.get("message") or {}).get("content", "").strip()
                    tokens = (data.get("usage") or {}).get("total_tokens", 0)
                    if content:
                        return content, tokens
                    last_err = "empty response from model"
                elif resp.status_code in (429, 500, 502, 503):
                    last_err = f"HTTP {resp.status_code} (rate limit / server)"
                else:
                    raise RuntimeError(
                        f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
            time.sleep(self.cfg["retry_backoff_seconds"] * (attempt + 1))
        raise RuntimeError(f"OpenRouter call failed after retries: {last_err}")
