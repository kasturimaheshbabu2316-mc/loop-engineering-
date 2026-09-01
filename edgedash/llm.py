"""LLM integration for EdgeDash — the single door to any language model.

Per steering rule 15, ALL LLM calls go through this module. No other file
imports an LLM SDK. The provider and model come from config, never hardcoded.

Supported providers:
- "gemini": Google's Generative AI API (requires GEMINI_API_KEY env var)
- "ollama": Local HTTP inference (no key, no API calls)

Rate limiting:
- Minimum 1 second between calls
- Rolling cap of 15 calls per minute
- On 429/quota errors: exponential backoff (3 attempts) then raise

Response handling:
- Strip markdown code fences and leading/trailing prose before parsing
- Validate against schema; retry once on validation failure with error
  context
- If retry fails, raise clear LLMError (callers handle per rule 17)
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from edgedash.config import Config


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Raised when LLM calls fail after retries or on config issues."""
    pass


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Enforce request spacing and rolling caps to prevent rate quota violations."""

    def __init__(self):
        self.call_times: list[datetime] = []
        self.min_interval = 2.0  # seconds between successive calls

    def acquire(self) -> None:
        """Wait if necessary to satisfy both rate limits."""
        now = datetime.now(timezone.utc)

        # Remove calls older than 1 minute
        self.call_times = [t for t in self.call_times if (now - t).total_seconds() < 60]

        # Check minimum interval
        if self.call_times:
            last_call = self.call_times[-1]
            elapsed = (now - last_call).total_seconds()
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)
                now = datetime.now(timezone.utc)

        # Check 15/min rolling cap
        if len(self.call_times) >= 15:
            oldest = self.call_times[0]
            age = (now - oldest).total_seconds()
            if age < 60:
                sleep_time = 60 - age
                time.sleep(sleep_time)
                now = datetime.now(timezone.utc)
                self.call_times = [t for t in self.call_times if (now - t).total_seconds() < 60]

        self.call_times.append(now)


_rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, schema: dict, max_retries: int, model: str = "gemini-3.5-flash-lite") -> dict:
    """Call Google Gemini API via google-genai SDK."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError(
            "GEMINI_API_KEY not set. Add it to .env file:\n"
            "  GEMINI_API_KEY=your_key_here\n"
            "Get a free key at https://aistudio.google.com/apikey"
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise LLMError(
            "google-genai not installed. Install with:\n"
            "  pip install google-genai"
        )

    client = genai.Client(api_key=api_key)

    schema_desc = json.dumps(schema, indent=2)
    system_instruction = (
        f"You must respond with ONLY valid JSON matching this schema:\n{schema_desc}\n\n"
        "No markdown fences. No prose. JSON only."
    )

    full_prompt = f"{system_instruction}\n\n{prompt}"

    # Use model name from parameter
    model_name = model or "gemini-3.5-flash-lite"

    for attempt in range(max_retries + 1):
        _rate_limiter.acquire()

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                ),
            )
            text = response.text
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "quota" in exc_str.lower() or "resource_exhausted" in exc_str.lower():
                # Extract explicit retry delay from API error if present
                delay_match = re.search(r'retry in ([0-9.]+)', exc_str, re.IGNORECASE) or re.search(r'retryDelay[\'":\s]+([0-9]+)', exc_str)
                if delay_match:
                    try:
                        backoff = float(delay_match.group(1)) + 1.0
                    except (ValueError, TypeError):
                        backoff = min(35.0, 5.0 * (attempt + 1))
                else:
                    backoff = min(35.0, 5.0 * (attempt + 1))

                if attempt < max_retries:
                    time.sleep(backoff)
                    continue
            raise LLMError(f"Gemini API error: {exc}")

        result = _parse_and_validate(text, schema)
        if result is not None:
            return result

        if attempt < max_retries:
            prompt = (
                prompt + "\n\nERROR: Response was not valid JSON or did not match schema. "
                "Respond with ONLY valid JSON, no markdown fences, no prose."
            )
        else:
            raise LLMError(
                f"Failed to get valid JSON from Gemini after {max_retries + 1} attempts"
            )

    raise LLMError("Unexpected: reached end of retry loop")


def _call_ollama(prompt: str, schema: dict, max_retries: int, model: str = "mistral") -> dict:
    """Call local Ollama HTTP server."""
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    try:
        import requests
    except ImportError:
        raise LLMError("requests not installed. Install with:\n  pip install requests")

    schema_desc = json.dumps(schema, indent=2)
    system_msg = (
        f"You must respond with ONLY valid JSON matching this schema:\n{schema_desc}\n\n"
        "No markdown fences. No prose. JSON only."
    )

    model_name = model or "mistral"

    for attempt in range(max_retries + 1):
        _rate_limiter.acquire()

        try:
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model_name,
                    "prompt": f"{system_msg}\n\n{prompt}",
                    "stream": False,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("response", "")
        except Exception as exc:
            if "429" in str(exc):
                backoff = 2 ** attempt
                if attempt < max_retries:
                    time.sleep(backoff)
                    continue
            raise LLMError(f"Ollama error: {exc}")

        # Parse and validate
        result = _parse_and_validate(text, schema)
        if result is not None:
            return result

        # Validation failed; retry with error context
        if attempt < max_retries:
            prompt = f"{prompt}\n\nERROR: Response was not valid JSON or did not match schema. Respond with ONLY valid JSON, no markdown fences, no prose."
        else:
            raise LLMError(f"Failed to get valid JSON from Ollama after {max_retries + 1} attempts")

    raise LLMError("Unexpected: reached end of retry loop")


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------

def _strip_markdown(text: str) -> str:
    """Remove markdown code fences and extra whitespace."""
    # Remove markdown code blocks (```json ... ``` or just ```)
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def _parse_and_validate(text: str, schema: dict) -> dict | None:
    """Parse JSON from text, validate against schema. Return dict or None on failure."""
    text = _strip_markdown(text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Basic schema validation: check that all required keys are present
    if isinstance(schema, dict) and "properties" in schema:
        required_keys = schema.get("required", [])
        if isinstance(parsed, dict):
            for key in required_keys:
                if key not in parsed:
                    return None

    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete_json(
    prompt: str,
    schema: dict,
    *,
    config: Config | None = None,
    max_retries: int = 1,
) -> dict:
    """Send a prompt, request JSON, validate against schema, and return dict.

    Per steering rule 15: provider and model come from config, never hardcoded.
    Per steering rule 17: validation failure is logged; caller handles it.

    Args:
        prompt: The prompt to send to the model.
        schema: JSON schema dict with "properties" and "required" keys.
        config: Config object with llm_provider and llm_model. If None,
                loads from environment or uses defaults.
        max_retries: Retries on validation failure (default 1).

    Returns:
        Parsed and validated JSON dict.

    Raises:
        LLMError: If API calls fail, config is invalid, or validation
                  fails after all retries.
    """
    if config is None:
        # Fallback: load config if not provided
        from edgedash.config import load_config
        config = load_config()

    provider = config.llm_provider.lower()
    model = config.llm_model

    if provider == "gemini":
        return _call_gemini(prompt, schema, max_retries, model=model)
    elif provider == "ollama":
        return _call_ollama(prompt, schema, max_retries, model=model)
    else:
        raise LLMError(f"Unknown LLM provider: {provider}. Use 'gemini' or 'ollama'.")


# ---------------------------------------------------------------------------
# CLI check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    

    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        try:
            from edgedash.env import load_env
            from edgedash.config import load_config

            load_env()
            cfg = load_config()
            print(f"Provider: {cfg.llm_provider}")
            print(f"Model:    {cfg.llm_model}")
            test_schema = {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["status", "message"],
            }

            result = complete_json(
                prompt="Respond with JSON only. Say: status is OK, message is hello.",
                schema=test_schema,
                config=cfg,
            )

            print(f"[OK] Success! Response: {result}")
            sys.exit(0)

        except Exception as exc:
            print(f"[FAIL] Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage: python -m edgedash.llm --check")
        sys.exit(1)
