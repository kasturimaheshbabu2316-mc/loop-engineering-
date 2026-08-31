"""Shared HTTP helper for EdgeDash.

This is the ONLY place in the project that performs HTTP requests.
All other modules must import and call get_json() from here.

Why requests? It provides connection pooling, timeout support, and
session reuse that the standard library's urllib does not offer cleanly,
saving meaningful development effort and keeping the code readable.
Install with: pip install requests==2.32.3
"""

from __future__ import annotations

import time
from typing import Any

import requests

# --------------------------------------------------------------------------
# Custom exception
# --------------------------------------------------------------------------

class SourceError(Exception):
    """Raised when an HTTP request fails after all retries."""


# --------------------------------------------------------------------------
# Module-level session (connection pooling, shared headers)
# --------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "EdgeDash/0.1 (+https://github.com/edgedash; career-intelligence-agent)"
    ),
})

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_DEFAULT_TIMEOUT: int = 10       # seconds
_DEFAULT_RETRIES: int = 2        # total attempts = 1 + retries
_BASE_BACKOFF: float = 1.0       # seconds; doubles on each retry


# --------------------------------------------------------------------------
# Public helper
# --------------------------------------------------------------------------

def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    retries: int = _DEFAULT_RETRIES,
) -> Any:
    """Fetch a URL and return the parsed JSON body.

    Implements:
    - 10-second timeout by default (steering rule 11)
    - 2 retry attempts with exponential backoff (1s, 2s)
    - Real User-Agent header via the module session
    - Raises SourceError on any persistent failure

    Args:
        url:     The URL to fetch (HTTPS preferred).
        params:  Optional query parameters.
        headers: Optional extra headers merged on top of session defaults.
        timeout: Per-request timeout in seconds (default: 10).
        retries: Number of additional attempts after the first (default: 2).

    Returns:
        Parsed JSON (dict, list, or scalar depending on the endpoint).

    Raises:
        SourceError: If all attempts fail.
    """
    attempt = 0
    last_error: Exception | None = None

    while attempt <= retries:
        if attempt > 0:
            wait = _BASE_BACKOFF * (2 ** (attempt - 1))
            time.sleep(wait)

        try:
            response = _SESSION.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout as exc:
            last_error = exc
        except requests.exceptions.HTTPError as exc:
            last_error = exc
            # Don't retry client errors (4xx) — they won't resolve
            if response.status_code < 500:
                break
        except requests.exceptions.RequestException as exc:
            last_error = exc

        attempt += 1

    raise SourceError(
        f"Request to {url} failed after {attempt} attempt(s): {last_error}"
    ) from last_error