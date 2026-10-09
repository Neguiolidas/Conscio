"""Decision via logprobs on an OpenAI-compatible endpoint (v4.9 item 13).

openjev/easy.py port: max_tokens=1, temperature=0.0, logprobs of the
FIRST token are the answer (system prompt says so). _probs_from_top
returns None when the label does not appear in the top-k — the engine
raises instead of inventing numbers (the original's honesty rule).
urllib only, no torch, no SDK.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from . import decision


class JevError(RuntimeError):
    """The label never appeared in the top-k: no invented numbers."""


class OpenAICompatJev:
    """Any OpenAI-compatible endpoint (Ollama/LM Studio/vLLM/OpenRouter/OpenAI)."""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 *, retries_429: int = 3, backoff_s: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.retries_429 = retries_429
        self.backoff_s = backoff_s

    # tests override this with a fixture
    def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})
        last: Exception | None = None
        for attempt in range(self.retries_429 + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except Exception as exc:  # 429/5xx retry
                last = exc
                if attempt < self.retries_429:
                    time.sleep(self.backoff_s * (2 ** attempt))
        raise JevError(f"chat failed after {self.retries_429 + 1} tries: {last}")

    def ask(self, question: decision.Choice | decision.Score | decision.Noul,
            state: str) -> dict[str, Any]:
        """One forward pass; the first token IS the answer."""
        labels = build_user_text(question, state)
        system = ("You are a decision engine. Your FIRST generated token is the"
                  " answer: reply with exactly one of the listed labels and"
                  " nothing else.")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": labels}],
            "max_tokens": 1,
            "temperature": 0.0,
            "logprobs": True,
            "top_logprobs": 20,
        }
        data = self._chat(payload)
        top = (data.get("choices") or [{}])[0].get("logprobs", {}).get("content")
        if not top:
            raise JevError("endpoint returned no logprobs")
        probs = _probs_from_top(top[0].get("top_logprobs") or [], labels)
        if probs is None:
            raise JevError("the label never appeared in the top-k")
        if isinstance(question, decision.Noul):
            return decision.render_answer(
                labels, "noul", [probs.get("Yes", 0.0)])
        if isinstance(question, decision.Choice):
            opts = list(question.options)
            plist = [probs.get(o, 0.0) for o in opts]
            return decision.render_answer(labels, "choice", plist, options=opts)
        # score: levels mapped to digits
        levels = question.levels
        plist = [probs.get(str(i + 1), 0.0) for i in range(levels)]
        return decision.render_answer(labels, "score", plist)


def build_user_text(question, state: str) -> str:
    """Labels mapped to letters (choice), digits (score), Yes/No (noul)."""
    if isinstance(question, decision.Noul):
        return f"{state}\n\nQuestion (answer Yes or No): {question.question_hint}"
    if isinstance(question, decision.Choice):
        letters = "ABCDEFGH"[:len(question.options)]
        lines = "\n".join(f"{l}) {o}" for l, o in zip(letters, question.options))
        return (f"{state}\n\nChoose one (reply with the letter only):\n{lines}")
    levels = question.levels
    digits = " ".join(str(i + 1) for i in range(levels))
    return f"{state}\n\nRate 1-{levels} (reply with the digit only): {digits}"


def _probs_from_top(top_logprobs: list[dict], labels: str) -> dict[str, float] | None:
    """Top-k logprobs -> probabilities keyed by the ORIGINAL label text.

    Returns None when no listed label appears (the honesty rule: never
    invent). Falls back case-insensitive for Yes/No.
    """
    probs: dict[str, float] = {}
    for entry in top_logprobs:
        token = (entry.get("token") or "").strip()
        logprob = entry.get("logprob")
        if token is None or logprob is None:
            continue
        p = 2.718281828459045 ** logprob
        for label in labels.replace("(", " ").replace(")", " ").split():
            if label == token:
                probs[label] = probs.get(label, 0.0) + p
                break
            if label.lower() == token.lower() and label in ("Yes", "No"):
                probs[label] = probs.get(label, 0.0) + p
                break
    return probs if probs else None
