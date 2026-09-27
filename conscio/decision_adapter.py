# conscio/decision_adapter.py
"""Decision adapter for Jev-like typed decision models (v4.8, S3).

Implements the POST /v1/systemone contract for typed decisions (noul, choice, score).
Stdlib only (urllib, dataclasses). All code, docstrings, and error messages in English.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import adapter_config

DECISION_TYPES = ("experiential", "typesafe", "systemone")
QUESTION_TYPES = ("noul", "choice", "score")

DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_S = 10.0

PRESETS: dict[str, dict[str, str]] = {
    "experiential": {
        "base_url": "https://api.experientiallabs.ai",
        "model": DEFAULT_MODEL,
        "api_key_env": "EXPERIENTIAL_API_KEY",
    },
    "typesafe": {
        "base_url": "https://api.typesafe.ai",
        "model": DEFAULT_MODEL,
        "api_key_env": "TYPESAFE_API_KEY",
    },
    "systemone": {
        "model": DEFAULT_MODEL,
    },
}

KNOWN_KEYS = frozenset({
    "type", "model", "base_url", "api_key_env", "api_key_file", "timeout_s",
})

_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class DecisionError(Exception):
    """Raised when transport, deadline, or response validation fails."""

    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(detail or status)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Answer:
    type: str                       # "noul" | "choice" | "score"
    value: str | float              # choice: chosen option; noul: [0,1]; score: position
    probabilities: dict[str, float] # {} for noul
    confidence: float | None        # None for noul


@dataclass(frozen=True)
class Decision:
    model: str | None               # data["model"] if string, else None
    answers: dict[str, Answer]


def _finite_float(value: Any) -> float | None:
    """Return finite float if value is a non-bool number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _url_ok(url: str) -> bool:
    """Allow https:// anywhere; http:// only on 127.0.0.1 or localhost."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname) and not parts.username and not parts.password
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"):
        return not parts.username and not parts.password
    return False


def _valid_env_name(v: Any) -> bool:
    return isinstance(v, str) and 1 <= len(v) <= 128 and bool(_ENV_RE.match(v))


@dataclass(frozen=True)
class DecisionAdapter:
    type: str
    base_url: str
    model: str
    api_key_env: str
    api_key_file: str | None
    timeout_s: float
    api_key: str = field(repr=False)   # Key is never exposed in repr or logs

    def __post_init__(self) -> None:
        if self.type not in DECISION_TYPES:
            raise ValueError(f"Invalid type {self.type!r}; must be one of {DECISION_TYPES}")
        if not isinstance(self.base_url, str) or not _url_ok(self.base_url):
            raise ValueError(f"Invalid base_url: {self.base_url!r}")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float)):
            raise ValueError(f"timeout_s must be a number, got {self.timeout_s!r}")
        if not math.isfinite(self.timeout_s) or not self.timeout_s > 0:
            raise ValueError(f"timeout_s must be finite and > 0, got {self.timeout_s!r}")
        if not isinstance(self.api_key, str) or not self.api_key:
            raise ValueError("api_key must be a non-empty string")
        if not (self.api_key.isascii() and self.api_key.isprintable()):
            raise ValueError("api_key must contain only printable ASCII characters")

    def decide(self, state: Any, questions: dict[str, dict]) -> Decision:
        """Query the model with state and questions, returning a typed Decision.

        Validates state and questions locally first (raising ValueError on caller errors).
        After local validation, only raises DecisionError.
        """
        # 1. Local validation before any I/O (raises ValueError)
        try:
            json.dumps(state, allow_nan=False)
        except (TypeError, ValueError) as err:
            raise ValueError(f"state is not valid JSON (allow_nan=False): {err}") from None

        if not isinstance(questions, dict) or not questions:
            raise ValueError("questions must be a non-empty dict")

        try:
            json.dumps(questions, allow_nan=False)
        except (TypeError, ValueError) as err:
            raise ValueError(f"questions is not valid JSON (allow_nan=False): {err}") from None

        for qid, q in questions.items():
            if not isinstance(qid, str) or not qid.strip():
                raise ValueError("question ids must be non-empty strings")
            if not isinstance(q, dict):
                raise ValueError(f"Question {qid!r} must be a dict")
            qtype = q.get("type")
            if qtype == "boolean":
                raise ValueError(f"Question {qid!r} has type 'boolean'; use 'noul' instead")
            if qtype not in QUESTION_TYPES:
                raise ValueError(
                    f"Question {qid!r} has invalid type {qtype!r}; must be one of {QUESTION_TYPES}"
                )
            crit = q.get("criteria")
            if qtype == "choice":
                if crit is None or not isinstance(crit, (dict, list)) or not (1 <= len(crit) <= 255):
                    raise ValueError(
                        f"Question {qid!r} of type 'choice' requires criteria with 1 to 255 options"
                    )
            elif qtype == "score":
                if crit is None or not isinstance(crit, (dict, list)) or not (1 <= len(crit) <= 10):
                    raise ValueError(
                        f"Question {qid!r} of type 'score' requires criteria with 1 to 10 levels"
                    )

        # 2-5. Transport and response validation (only raises DecisionError)
        raw = self._execute_transport(state, questions)
        return self._parse_response(raw, questions)

    def _execute_transport(self, state: Any, questions: dict[str, dict]) -> bytes:
        err_status: str | None = None
        raw: bytes | None = None

        try:
            payload = json.dumps(
                {"model": self.model, "questions": questions, "state": state},
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/v1/systemone"

            deadline = time.monotonic() + self.timeout_s
            attempt = 0

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    err_status = "timeout"
                    break

                try:
                    request = urllib.request.Request(
                        url, data=payload, headers=headers, method="POST"
                    )
                    with urllib.request.urlopen(request, timeout=remaining) as response:
                        raw = response.read()
                    break
                except urllib.error.HTTPError as err:
                    if err.code in (429, 529):
                        delay = min(2.0 ** attempt, max(deadline - time.monotonic(), 0.0))
                        attempt += 1
                        if delay > 0:
                            time.sleep(delay)
                        continue
                    err_status = f"http_{err.code}"
                    break
                except urllib.error.URLError as err:
                    reason = getattr(err, "reason", None)
                    if isinstance(reason, TimeoutError):
                        if deadline - time.monotonic() <= 0:
                            err_status = "timeout"
                        else:
                            err_status = "network"
                    else:
                        err_status = "network"
                    break
                except (TimeoutError, ConnectionError, OSError) as err:
                    if isinstance(err, TimeoutError):
                        if deadline - time.monotonic() <= 0:
                            err_status = "timeout"
                        else:
                            err_status = "network"
                    else:
                        err_status = "network"
                    break
                except Exception:
                    err_status = "network"
                    break
        except Exception:
            err_status = "network"

        # Transport errors must strictly maintain the invariant:
        # __cause__ is None and __context__ is None, leaving zero reference
        # to original transport exceptions (preventing secret key leakage).
        if err_status is None and raw is None:
            err_status = "network"

        if err_status is not None:
            err = DecisionError(err_status)
            setattr(err, "__cause__", None)
            setattr(err, "__context__", None)
            raise err

        assert raw is not None
        return raw

    def _parse_response(self, raw: bytes, questions: dict[str, dict]) -> Decision:
        try:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as err:
                raise DecisionError("malformed", f"invalid JSON or encoding: {err}") from None

            if not isinstance(data, dict):
                raise DecisionError("malformed", "response is not a JSON object")

            model_resp = data.get("model")
            model_str = model_resp if isinstance(model_resp, str) else None

            answers_raw = data.get("answers")
            if not isinstance(answers_raw, dict):
                raise DecisionError("malformed", "answers field missing or not an object")

            parsed_answers: dict[str, Answer] = {}
            for qid, q in questions.items():
                if qid not in answers_raw:
                    raise DecisionError("malformed", f"missing answer for question {qid!r}")
                ans_item = answers_raw[qid]
                if not isinstance(ans_item, dict):
                    raise DecisionError("malformed", f"answer for {qid!r} must be an object")

                ans_type = ans_item.get("type")
                if ans_type != q["type"]:
                    raise DecisionError(
                        "malformed",
                        f"answer type {ans_type!r} does not match question type {q['type']!r}",
                    )

                if q["type"] == "choice":
                    choice_val = ans_item.get("choice")
                    if not isinstance(choice_val, str):
                        raise DecisionError(
                            "malformed", f"choice value for {qid!r} must be str, got {type(choice_val)}"
                        )
                    crit = q.get("criteria", {})
                    allowed = set(crit.keys()) if isinstance(crit, dict) else set(crit)
                    if choice_val not in allowed:
                        raise DecisionError(
                            "malformed", f"choice {choice_val!r} not in criteria {allowed}"
                        )
                    probs_raw = ans_item.get("probabilities")
                    if not isinstance(probs_raw, dict):
                        raise DecisionError("malformed", f"probabilities for {qid!r} must be an object")
                    if set(probs_raw.keys()) != allowed:
                        raise DecisionError(
                            "malformed",
                            f"probabilities keys {set(probs_raw.keys())} != criteria keys {allowed}",
                        )
                    probs_clean: dict[str, float] = {}
                    for k, v in probs_raw.items():
                        f = _finite_float(v)
                        if f is None or not (0.0 <= f <= 1.0):
                            raise DecisionError(
                                "malformed", f"probability for {k!r} must be finite in [0, 1]"
                            )
                        probs_clean[k] = f
                    conf_f = _finite_float(ans_item.get("confidence"))
                    if conf_f is None or not (0.0 <= conf_f <= 1.0):
                        raise DecisionError(
                            "malformed", "confidence must be finite in [0, 1]"
                        )
                    parsed_answers[qid] = Answer(
                        type="choice",
                        value=choice_val,
                        probabilities=probs_clean,
                        confidence=conf_f,
                    )

                elif q["type"] == "noul":
                    noul_val = _finite_float(ans_item.get("noul"))
                    if noul_val is None or not (0.0 <= noul_val <= 1.0):
                        raise DecisionError(
                            "malformed", f"noul value for {qid!r} must be finite in [0, 1]"
                        )
                    parsed_answers[qid] = Answer(
                        type="noul",
                        value=noul_val,
                        probabilities={},
                        confidence=None,
                    )

                elif q["type"] == "score":
                    score_val = _finite_float(ans_item.get("score"))
                    if score_val is None:
                        raise DecisionError(
                            "malformed", f"score value for {qid!r} must be a finite number"
                        )
                    probs_raw = ans_item.get("probabilities")
                    if not isinstance(probs_raw, dict):
                        raise DecisionError("malformed", f"probabilities for {qid!r} must be an object")
                    probs_clean = {}
                    for k, v in probs_raw.items():
                        if not isinstance(k, str):
                            raise DecisionError(
                                "malformed", f"score probability key {k!r} must be str"
                            )
                        f = _finite_float(v)
                        if f is None or not (0.0 <= f <= 1.0):
                            raise DecisionError(
                                "malformed", f"probability for level {k!r} must be finite in [0, 1]"
                            )
                        probs_clean[k] = f
                    conf_f = _finite_float(ans_item.get("confidence"))
                    if conf_f is None or not (0.0 <= conf_f <= 1.0):
                        raise DecisionError(
                            "malformed", "confidence must be finite in [0, 1]"
                        )
                    parsed_answers[qid] = Answer(
                        type="score",
                        value=score_val,
                        probabilities=probs_clean,
                        confidence=conf_f,
                    )

            return Decision(model=model_str, answers=parsed_answers)
        except DecisionError:
            raise
        except Exception as exc:
            # ONLY response decode/validation exceptions embed str(exc)
            raise DecisionError("malformed", str(exc)) from None


def load_decision_adapter(cfg: dict | None = None) -> DecisionAdapter | None | str:
    """Load and validate DecisionAdapter configuration without opening any connection.

    Returns:
      DecisionAdapter on success.
      None if 'decision_adapter' block is absent.
      'bad_config' if configuration is invalid.
      'no_key' if API key cannot be resolved from env, vault, or key file.
    Never raises.
    """
    if cfg is None:
        try:
            cfg = adapter_config.load_config()
        except Exception:
            return "bad_config"

    if not isinstance(cfg, dict):
        return "bad_config"

    if "decision_adapter" not in cfg or cfg.get("decision_adapter") is None:
        return None

    block = cfg.get("decision_adapter")
    if not isinstance(block, dict):
        return "bad_config"

    # Inline api_key is strictly prohibited (D6)
    if "api_key" in block:
        return "bad_config"

    # Any unknown key -> bad_config (D10)
    for k in block:
        if k not in KNOWN_KEYS:
            return "bad_config"

    atype = block.get("type")
    if not isinstance(atype, str) or atype not in DECISION_TYPES:
        return "bad_config"

    preset = PRESETS[atype]

    # base_url validation
    base_url = block.get("base_url") or preset.get("base_url")
    if atype == "systemone" and not block.get("base_url"):
        return "bad_config"
    if not isinstance(base_url, str) or not _url_ok(base_url):
        return "bad_config"
    base_url = base_url.rstrip("/")

    # model validation
    model = block.get("model") or preset.get("model") or DEFAULT_MODEL
    if not isinstance(model, str) or not model.strip():
        return "bad_config"

    # api_key_env validation
    api_key_env = block.get("api_key_env") or preset.get("api_key_env")
    if atype == "systemone" and not block.get("api_key_env"):
        return "bad_config"
    if not isinstance(api_key_env, str) or not _valid_env_name(api_key_env):
        return "bad_config"

    # api_key_file validation
    api_key_file = block.get("api_key_file")
    if api_key_file is not None and not isinstance(api_key_file, str):
        return "bad_config"

    # timeout_s validation
    raw_timeout = block.get("timeout_s", DEFAULT_TIMEOUT_S)
    timeout_s = _finite_float(raw_timeout)
    if timeout_s is None or timeout_s <= 0:
        return "bad_config"

    # Resolve key
    key = adapter_config.resolve_api_key(api_key_env, key_file=api_key_file)
    if not key:
        return "no_key"

    try:
        return DecisionAdapter(
            type=atype,
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            api_key_file=api_key_file,
            timeout_s=timeout_s,
            api_key=key,
        )
    except ValueError:
        return "bad_config"
