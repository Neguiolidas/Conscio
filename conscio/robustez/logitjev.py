"""Decision via logprobs on an OpenAI-compatible endpoint (v4.9 item 13).

openjev/easy.py port: max_tokens=1, temperature=0.0, logprobs of the
FIRST token are the answer (system prompt says so). _probs_from_top
returns None when the label does not appear in the top-k — the engine
raises instead of inventing numbers (the original's honesty rule).
urllib only, no torch, no SDK.
"""
from __future__ import annotations

import json
import math
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
        prompt = build_user_text(question, state)
        system = ("You are a decision engine. Your FIRST generated token is the"
                  " answer: reply with exactly one of the listed labels and"
                  " nothing else.")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "max_tokens": 1,
            "temperature": 0.0,
            "logprobs": True,
            "top_logprobs": 20,
        }
        data = self._chat(payload)
        top = (data.get("choices") or [{}])[0].get("logprobs", {}).get("content")
        if not top:
            raise JevError("endpoint returned no logprobs")
        labels = _answer_labels(question)
        probs = _probs_from_top(top[0].get("top_logprobs") or [], labels)
        if probs is None:
            raise JevError("the label never appeared in the top-k")
        if isinstance(question, decision.Noul):
            # The 'question' field carries the real question, not the prompt.
            question_text = question.question_hint or state[:120]
            return decision.render_answer(
                question_text, "noul", [probs.get("Yes", 0.0)])
        # Choice answers with the option LETTER, score with the level DIGIT:
        # probs is keyed by those labels, in option/level order.
        plist = [probs.get(label, 0.0) for label in labels]
        if isinstance(question, decision.Choice):
            return decision.render_answer(
                state[:120], "choice", plist, options=list(question.options))
        return decision.render_answer(state[:120], "score", plist)


# max_tokens=1 reads one token, so every label must be one character:
# A-Z for options, 0-9 for score levels ("10" splits into "1" + "0" on
# digit-splitting tokenizers and would be read as level 1).
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _answer_labels(question: decision.Choice | decision.Score | decision.Noul) -> list[str]:
    """The exact tokens the prompt asks for: Yes/No, option letters, level digits."""
    if isinstance(question, decision.Noul):
        return ["Yes", "No"]
    if isinstance(question, decision.Choice):
        if len(question.options) > len(_LETTERS):
            raise ValueError(
                f"logitjev labels options A-Z: {len(question.options)} options"
                f" exceed {len(_LETTERS)}")
        return list(_LETTERS[:len(question.options)])
    return [str(i) for i in range(question.levels)]


def build_user_text(question, state: str) -> str:
    """Labels mapped to letters (choice), digits (score), Yes/No (noul)."""
    if isinstance(question, decision.Noul):
        return f"{state}\n\nQuestion (answer Yes or No): {question.question_hint}"
    labels = _answer_labels(question)
    if isinstance(question, decision.Choice):
        lines = "\n".join(f"{l}) {o}" for l, o in zip(labels, question.options))
        return (f"{state}\n\nChoose one (reply with the letter only):\n{lines}")
    return (f"{state}\n\nRate 0-{question.levels - 1}, 0 lowest"
            f" (reply with the digit only): {' '.join(labels)}")


def _probs_from_top(top_logprobs: list[dict], labels: list[str]) -> dict[str, float] | None:
    """Top-k logprobs -> probabilities keyed by the label the prompt asked for.

    Only the listed labels count: a token that merely appears somewhere in
    the prompt (state text, option text) is not an answer. Returns None when
    no listed label appears (the honesty rule: never invent). Falls back
    case-insensitive for Yes/No.
    """
    probs: dict[str, float] = {}
    for entry in top_logprobs:
        token = (entry.get("token") or "").strip()
        logprob = entry.get("logprob")
        if logprob is None:
            continue
        label = _match_label(token, labels)
        if label is not None:
            probs[label] = probs.get(label, 0.0) + math.exp(logprob)
    return probs or None


def _match_label(token: str, labels: list[str]) -> str | None:
    if token in labels:
        return token
    for label in labels:
        if label in ("Yes", "No") and label.lower() == token.lower():
            return label
    return None
