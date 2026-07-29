# OpenRouter LLM client — shared by parse and topic_manager

import json
import re
import time

from openai import OpenAI

_BASE_URL = "https://openrouter.ai/api/v1"
_MAX_RETRIES = 3
_RETRY_DELAY = 2.0   # seconds between retries

_JSON_SYSTEM_SUFFIX = (
    "\n\nReturn ONLY a raw JSON object or array — "
    "no markdown fences, no preamble, no explanation, no trailing text."
)


def make_client(api_key: str) -> OpenAI:
    return OpenAI(base_url=_BASE_URL, api_key=api_key)


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


def call(system: str, user: str, model: str, api_key: str) -> str:
    """Plain-text LLM call with retries on empty/null responses."""
    client = make_client(api_key)
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=8192,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            content = resp.choices[0].message.content
            if content:
                return content.strip()
            last_exc = ValueError("Model returned empty content")
        except Exception as e:
            last_exc = e

        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_DELAY)

    raise RuntimeError(f"LLM call failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def call_json(system: str, user: str, model: str, api_key: str) -> dict | list:
    """JSON LLM call with retries and multi-strategy parsing.

    Does NOT use response_format — it is unsupported by many OpenRouter models
    and can silently produce empty content.  Instead we extract JSON from the
    raw text via _extract_json, which handles markdown fences and leading prose.
    On parse failures the retry prompt explicitly demands raw JSON.
    """
    client = make_client(api_key)
    last_exc: Exception = RuntimeError("No attempts made")
    retry_suffix = ""

    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system + _JSON_SYSTEM_SUFFIX},
                    {"role": "user",   "content": user + retry_suffix},
                ],
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("Model returned empty content")
            return _extract_json(content)
        except Exception as e:
            last_exc = e
            retry_suffix = (
                "\n\nCRITICAL: your previous response could not be parsed as JSON. "
                "Return ONLY a raw JSON object or array — no markdown fences, "
                "no explanation, no preamble, no trailing text."
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY)

    raise RuntimeError(
        f"JSON call failed after {_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc
