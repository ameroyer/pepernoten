# LLM client — dispatches each call to the Claude CLI (`claude -p`, uses the local Claude
# Pro/Max login, no API key) or to OpenRouter, and tracks token usage / cost either way.

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from openai import OpenAI

_BASE_URL = "https://openrouter.ai/api/v1"
_MAX_RETRIES = 3
_RETRY_DELAY = 2.0    # seconds between retries
_CLI_TIMEOUT = 900    # seconds per `claude -p` invocation (large topic syntheses need headroom)

_JSON_SYSTEM_SUFFIX = (
    "\n\nReturn ONLY a raw JSON object or array — "
    "no markdown fences, no preamble, no explanation, no trailing text."
)
_JSON_RETRY_SUFFIX = (
    "\n\nCRITICAL: your previous response could not be parsed as JSON. "
    "Return ONLY a raw JSON object or array — no markdown fences, "
    "no explanation, no preamble, no trailing text."
)

_CLI_MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")


# ──────────────────────────────────────────────────────────────────────────────
# Usage tracking
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None   # None when the provider didn't report cost
    model: str
    provider: str             # "openrouter" | "claude_cli"


class UsageTracker:
    """Accumulates Usage entries across however many LLM/vision calls it took to produce
    one note or topic file. Pass the same instance into every call() / call_json() for that
    note via the `tracker=` kwarg."""

    def __init__(self):
        self._lock = threading.Lock()
        self.entries: list[Usage] = []

    def add(self, usage: Usage | None):
        if usage is None:
            return
        with self._lock:
            self.entries.append(usage)

    def total_tokens(self) -> int:
        with self._lock:
            return sum(u.prompt_tokens + u.completion_tokens for u in self.entries)

    def total_cost(self) -> float | None:
        with self._lock:
            costs = [u.cost_usd for u in self.entries if u.cost_usd is not None]
        return sum(costs) if costs else None


def usage_from_openai_response(resp, model: str) -> Usage:
    """Build a Usage from a raw openai-SDK chat completion response (OpenRouter)."""
    u = getattr(resp, "usage", None)
    return Usage(
        prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        cost_usd=getattr(u, "cost", None),
        model=model,
        provider="openrouter",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Provider selection
# ──────────────────────────────────────────────────────────────────────────────

def make_client(api_key: str) -> OpenAI:
    return OpenAI(base_url=_BASE_URL, api_key=api_key)


def _claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def _is_claude_model(model: str) -> bool:
    m = model.lower()
    return "claude" in m or "anthropic/" in m or m in _CLI_MODEL_ALIASES


def _cli_model_alias(model: str) -> str:
    """Translate an OpenRouter-style model id ("anthropic/claude-haiku-4.5") into a
    `claude -p --model` alias ("haiku"). Full CLI model names pass through unchanged."""
    tail = model.rsplit("/", 1)[-1].lower()
    for alias in _CLI_MODEL_ALIASES:
        if alias in tail:
            return alias
    return model


def _resolve_provider(model: str, provider: str | None) -> str:
    if provider:
        return provider
    env = os.environ.get("PEPERNOTEN_LLM_PROVIDER", "auto")
    if env in ("openrouter", "claude_cli"):
        return env
    return "claude_cli" if _is_claude_model(model) and _claude_cli_available() else "openrouter"


# ──────────────────────────────────────────────────────────────────────────────
# Backends
# ──────────────────────────────────────────────────────────────────────────────

def _call_claude_cli(
    system: str, user: str, model: str, json_schema: dict | None = None,
) -> tuple[str | dict | list, Usage]:
    cmd = [
        "claude", "-p",
        "--model", _cli_model_alias(model),
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--safe-mode",
        "--system-prompt", system,
    ]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema)]

    try:
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=_CLI_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude CLI timed out after {_CLI_TIMEOUT}s") from e
    except FileNotFoundError as e:
        raise RuntimeError("claude CLI not found on PATH") from e

    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude CLI produced non-JSON output: {proc.stdout[:300]!r}") from e

    if payload.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(payload.get('result', ''))[:500]}")

    usage_raw = payload.get("usage") or {}
    usage = Usage(
        prompt_tokens=int(usage_raw.get("input_tokens", 0)),
        completion_tokens=int(usage_raw.get("output_tokens", 0)),
        cost_usd=payload.get("total_cost_usd"),
        model=model,
        provider="claude_cli",
    )

    if json_schema is not None and "structured_output" in payload:
        return payload["structured_output"], usage
    return payload.get("result") or "", usage


def _call_openrouter(
    system: str, user: str, model: str, api_key: str, max_tokens: int,
) -> tuple[str, Usage]:
    client = make_client(api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        extra_body={"usage": {"include": True}},
    )
    content = resp.choices[0].message.content or ""
    return content.strip(), usage_from_openai_response(resp, model)


def _dispatch(
    system: str, user: str, model: str, api_key: str, resolved_provider: str,
    max_tokens: int, json_schema: dict | None = None,
) -> tuple[str | dict | list, Usage]:
    if resolved_provider == "claude_cli":
        return _call_claude_cli(system, user, model, json_schema=json_schema)
    return _call_openrouter(system, user, model, api_key, max_tokens)


# ──────────────────────────────────────────────────────────────────────────────
# JSON extraction (used for providers/paths that don't have structured output)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | list:
    """
    Parse JSON from an LLM response, handling all common wrapping patterns.

    Tries in order:
    1. Direct parse of the stripped text
    2. Strip markdown code fences (```json … ``` or ``` … ```)
    3. Extract the first {...} or [...] block via a greedy regex
    """
    if not text or not text.strip():
        raise ValueError("Empty response from model")

    s = text.strip()

    # 1. Direct
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences
    defenced = re.sub(r"^```(?:json)?\s*\n?", "", s, flags=re.IGNORECASE)
    defenced = re.sub(r"\n?```\s*$", "", defenced).strip()
    try:
        return json.loads(defenced)
    except json.JSONDecodeError:
        pass

    # 3. First {...} or [...] block (greedy — handles leading/trailing prose)
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

    raise ValueError(f"No valid JSON found in response ({len(s)} chars): {s[:300]!r}")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def call(
    system: str, user: str, model: str, api_key: str = "", *,
    provider: str | None = None,
    tracker: UsageTracker | None = None,
    max_tokens: int = 8192,
    validate: Callable[[str], bool] | None = None,
    retry_hint: str = "",
) -> str:
    """Plain-text LLM call with retries on empty/invalid responses.

    `validate`/`retry_hint` let a caller enforce structural expectations (e.g. the
    ===SECTION=== markers the paper writer stage relies on) using the same retry-with-
    corrective-suffix mechanism call_json() uses for malformed JSON.
    """
    resolved = _resolve_provider(model, provider)
    last_exc: Exception = RuntimeError("No attempts made")
    extra = ""

    for attempt in range(_MAX_RETRIES):
        try:
            text, usage = _dispatch(system, user + extra, model, api_key, resolved, max_tokens)
        except Exception as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)
            continue

        if tracker is not None:
            tracker.add(usage)

        if not text or not text.strip():
            last_exc = ValueError("Model returned empty content")
        elif validate is None or validate(text):
            return text.strip()
        else:
            last_exc = ValueError("Response failed structural validation")

        if retry_hint:
            extra = f"\n\n{retry_hint}"
        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_DELAY)

    raise RuntimeError(f"LLM call failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def call_json(
    system: str, user: str, model: str, api_key: str = "", *,
    provider: str | None = None,
    tracker: UsageTracker | None = None,
    max_tokens: int = 8192,
    json_schema: dict | None = None,
) -> dict | list:
    """JSON LLM call with retries and multi-strategy parsing.

    Does NOT rely on OpenRouter's response_format — it is unsupported by many OpenRouter
    models and can silently produce empty content. Instead we extract JSON from the raw
    text via _extract_json, which handles markdown fences and leading prose. On the
    claude_cli provider, passing json_schema uses `--json-schema` for validated structured
    output directly; _extract_json is still the fallback if that's absent from the response.
    """
    resolved = _resolve_provider(model, provider)
    system_full = system + _JSON_SYSTEM_SUFFIX
    last_exc: Exception = RuntimeError("No attempts made")
    retry_suffix = ""

    for attempt in range(_MAX_RETRIES):
        try:
            raw, usage = _dispatch(system_full, user + retry_suffix, model, api_key, resolved, max_tokens, json_schema)
        except Exception as e:
            last_exc = e
            retry_suffix = _JSON_RETRY_SUFFIX
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)
            continue

        if tracker is not None:
            tracker.add(usage)

        try:
            if json_schema is not None and not isinstance(raw, str):
                return raw
            return _extract_json(raw if isinstance(raw, str) else json.dumps(raw))
        except Exception as e:
            last_exc = e
            retry_suffix = _JSON_RETRY_SUFFIX
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)

    raise RuntimeError(f"JSON call failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc
