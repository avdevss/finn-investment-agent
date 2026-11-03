# Shared Gemini client. Every model call in the project goes through here so that
# throttling, patient retries, disk caching and token accounting happen once.
#
# Model names come from .env (CHEAP_MODEL, STRONG_MODEL) so swapping is one line.
#
# There is no fallback chain. The free tier allows 20 generate-content requests per
# day per model, so the strong tier is a budget to be spent deliberately rather than
# a rung to slide off: every caller names the tier it wants, a failure is reported to
# that caller, and nothing is silently answered by a weaker model. What each row was
# actually produced by is recorded as model_used.

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

API_BASE: Final = "https://generativelanguage.googleapis.com/v1beta/models"
CACHE_DIR: Final = pathlib.Path(__file__).parent / "cache" / "llm"

Tier = Literal["cheap", "strong"]

# Free tier allows few requests per minute; one global gate for all callers.
MIN_INTERVAL: Final = float(os.getenv("LLM_MIN_INTERVAL", "4.0"))

RETRY_STATUS: Final = frozenset({408, 429, 500, 502, 503, 504, 509})

# A 503 on the strong tier clears in minutes, so waits grow but stay bounded.
# The strong read timeout is deliberately short: a congested model hangs rather
# than refusing, and hanging is the expensive failure.
_POLICY: Final[dict[str, dict[str, float]]] = {
    "cheap": {"attempts": 4, "base": 3.0, "cap": 45.0, "timeout": 60.0},
    "strong": {"attempts": 3, "base": 8.0, "cap": 120.0, "timeout": 40.0},
}

# Once a model is genuinely unavailable, stop paying the timeout cost on every later
# call in the run. Two consecutive exhausted calls, not one: a single 503 is how a
# healthy model answers under momentary load, and tripping on it would push a whole
# run onto the next rung for ten minutes over one unlucky request.
BREAKER_STRIKES: Final = 2
BREAKER_COOLDOWN: Final = 600.0

# A daily quota is not congestion. Google answers an exhausted free tier allowance
# with 429 and a RetryInfo of several hours, which no amount of backoff will outlast:
# retrying it burns the attempts, spends two strikes on a condition that will not
# clear, and leaves the log saying "HTTP 429" instead of saying the quota is gone.
# An advised wait longer than this is taken at face value and the model is set aside.
QUOTA_WALL: Final = 300.0

_last_call = 0.0
_tripped: dict[str, float] = {}
_strikes: dict[str, int] = {}


class LLMError(RuntimeError):
    """Every model in the chain refused or timed out."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cached_hits: int = 0
    by_model: dict[str, int] = field(default_factory=dict)
    # Requests actually sent, per model. The free tier caps these per day, so the
    # count that matters for the budget is calls, not tokens.
    calls_by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


usage = Usage()


@dataclass
class Result:
    text: str
    model_used: str
    tier_requested: Tier
    cached: bool
    prompt_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    def json(self) -> Any:
        return json.loads(self.text)


def _model_for(tier: Tier) -> str:
    key = "STRONG_MODEL" if tier == "strong" else "CHEAP_MODEL"
    name = os.getenv(key, "").strip()
    if not name:
        raise LLMError(f"{key} is not set in .env")
    if "preview" in name:
        raise LLMError(f"{key}={name} is a preview model")
    return name


def _strike(model: str) -> None:
    """One exhausted call. The breaker opens on the second in a row."""
    _strikes[model] = _strikes.get(model, 0) + 1
    if _strikes[model] < BREAKER_STRIKES:
        log.warning("%s exhausted its attempts (%d of %d strikes), still trying it",
                    model, _strikes[model], BREAKER_STRIKES)
        return
    _tripped[model] = time.monotonic() + BREAKER_COOLDOWN
    log.warning("%s exhausted %d calls in a row, skipping it for %.0f minutes",
                model, _strikes[model], BREAKER_COOLDOWN / 60)


def _wall(model: str, delay: float, detail: str) -> None:
    """Set a model aside for as long as it says it needs, no strikes involved."""
    _tripped[model] = time.monotonic() + delay
    _strikes[model] = BREAKER_STRIKES
    log.error("%s is unavailable for %.1f hours: %s", model, delay / 3600, detail)


def _clear(model: str) -> None:
    _strikes.pop(model, None)
    _tripped.pop(model, None)


def _is_tripped(model: str) -> bool:
    until = _tripped.get(model)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _tripped[model]
        return False
    return True


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _cache_key(tier: Tier, model: str, prompt: str, system: str | None,
               schema: dict | None, temperature: float, max_tokens: int) -> str:
    # Keyed on the request, model name included, so that editing a model in .env
    # correctly invalidates its entries rather than serving the old one's answers.
    payload = json.dumps({"tier": tier, "model": model, "prompt": prompt,
                          "system": system, "schema": schema,
                          "temperature": temperature, "max_tokens": max_tokens},
                         sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _read_cache(key: str) -> Result | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("corrupt cache entry %s, ignoring", path.name)
        return None
    return Result(text=d["text"], model_used=d["model_used"],
                  tier_requested=d["tier_requested"], cached=True,
                  prompt_tokens=d["prompt_tokens"], output_tokens=d["output_tokens"])


def _write_cache(key: str, r: Result) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f"{key}.json.tmp"
    tmp.write_text(json.dumps({
        "text": r.text, "model_used": r.model_used, "tier_requested": r.tier_requested,
        "prompt_tokens": r.prompt_tokens, "output_tokens": r.output_tokens,
        "written_at": time.time(),
    }))
    tmp.replace(CACHE_DIR / f"{key}.json")


def _body(prompt: str, system: str | None, schema: dict | None,
          temperature: float, max_tokens: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if schema is not None:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    return body


def _extract(payload: dict) -> tuple[str, int, int]:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"no candidates: {str(payload.get('promptFeedback'))[:200]}")
    cand = candidates[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    if not text.strip():
        raise LLMError(f"empty text, finishReason={cand.get('finishReason')}")
    u = payload.get("usageMetadata", {})
    return text, int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))


def _call_once(model: str, body: dict, timeout: float, api_key: str) -> tuple[str, int, int]:
    _throttle()
    resp = requests.post(f"{API_BASE}/{model}:generateContent",
                         headers={"x-goog-api-key": api_key,
                                  "Content-Type": "application/json"},
                         json=body, timeout=timeout)
    if resp.status_code in RETRY_STATUS:
        raise _Retryable(resp.status_code, _advised_delay(resp), _error_message(resp))
    if resp.status_code >= 400:
        # A rejected request will be rejected identically forever, so it is not
        # retried. It is still raised as LLMError, because one malformed schema
        # must degrade to a skipped item rather than end a long run. The API's
        # own message is carried through: it is the only thing that says why.
        raise LLMError(f"HTTP {resp.status_code} from {model}: "
                       f"{_error_message(resp)}")
    return _extract(resp.json())


def _error_message(resp: requests.Response) -> str:
    """The API's own explanation, including which quota a 429 was."""
    try:
        error = resp.json().get("error", {})
    except ValueError:
        return resp.text[:300]

    parts = [str(error.get("message", ""))]
    for detail in error.get("details", []):
        for violation in detail.get("violations", []) or []:
            if violation.get("description"):
                parts.append(str(violation["description"]))
    return " | ".join(p for p in parts if p)[:300]


def _advised_delay(resp: requests.Response) -> float | None:
    """How long the API asked us to wait, from the header or from RetryInfo."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        details = resp.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    for detail in details:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


class _Retryable(Exception):
    def __init__(self, status: int, delay: float | None = None, detail: str = ""):
        super().__init__(f"HTTP {status}{': ' + detail if detail else ''}")
        self.status = status
        self.delay = delay          # what the API itself asked us to wait
        self.detail = detail


def _wait_for(exc: Exception, attempt: int, base: float, cap: float) -> float:
    if isinstance(exc, _Retryable) and exc.delay:
        return min(exc.delay, cap)
    return min(base * (2 ** attempt), cap) + random.uniform(0, 1.0)


def generate(prompt: str, *, tier: Tier = "cheap", system: str | None = None,
             schema: dict | None = None, temperature: float = 0.0,
             max_tokens: int = 4096) -> Result:
    """One model call on the named tier, cached on disk.

    The tier is the only model tried. An exhausted or walled model raises LLMError
    for the caller to record and skip, so nothing is written from a weaker model
    than the caller asked for.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise LLMError("GEMINI_API_KEY is not set in .env")

    model = _model_for(tier)
    key = _cache_key(tier, model, prompt, system, schema, temperature, max_tokens)

    hit = _read_cache(key)
    if hit is not None:
        usage.cached_hits += 1
        return hit

    if _is_tripped(model):
        raise LLMError(f"{model}: breaker open, not retried in this run")

    body = _body(prompt, system, schema, temperature, max_tokens)
    policy = _POLICY[tier]

    for attempt in range(int(policy["attempts"])):
        try:
            text, ptok, otok = _call_once(model, body, policy["timeout"], api_key)
        except LLMError as e:
            # Already a considered refusal: an empty candidate or a 4xx. Retrying
            # the same request would get the same answer.
            raise LLMError(f"{model}: {e}") from e
        except (_Retryable, requests.Timeout, requests.ConnectionError) as e:
            advised = getattr(e, "delay", None)
            if advised is not None and advised > QUOTA_WALL:
                _wall(model, advised, getattr(e, "detail", str(e)))
                raise LLMError(f"{model}: {e}") from e
            if attempt == int(policy["attempts"]) - 1:
                _strike(model)
                raise LLMError(f"{model}: {type(e).__name__} {e}") from e
            wait = _wait_for(e, attempt, policy["base"], policy["cap"])
            log.warning("%s on %s (%s), attempt %d/%d, sleeping %.0fs",
                        type(e).__name__, model, e, attempt + 1,
                        int(policy["attempts"]), wait)
            time.sleep(wait)
            continue

        _clear(model)
        result = Result(text=text, model_used=model, tier_requested=tier,
                        cached=False, prompt_tokens=ptok, output_tokens=otok)
        usage.calls += 1
        usage.prompt_tokens += ptok
        usage.output_tokens += otok
        usage.by_model[model] = usage.by_model.get(model, 0) + ptok + otok
        usage.calls_by_model[model] = usage.calls_by_model.get(model, 0) + 1
        _write_cache(key, result)
        return result

    raise LLMError(f"{model}: exhausted {int(policy['attempts'])} attempts")


def generate_json(prompt: str, *, schema: dict, tier: Tier = "cheap",
                  system: str | None = None,
                  max_tokens: int = 4096) -> tuple[Any, Result]:
    """generate() in JSON mode. Returns the parsed value and the call metadata."""
    r = generate(prompt, tier=tier, system=system, schema=schema,
                 temperature=0.0, max_tokens=max_tokens)
    try:
        return r.json(), r
    except json.JSONDecodeError as e:
        raise LLMError(f"{r.model_used} returned unparseable JSON: {e}") from e
