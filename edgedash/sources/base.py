"""Base Source protocol and registry for EdgeDash.

Every source must implement the Source protocol and register itself with
the @register decorator. Adding a new source never requires editing the
Fetcher — only decorating a new class in this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from edgedash.config import Config


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SOURCES: dict[str, type["Source"]] = {}


def register(cls: type["Source"]) -> type["Source"]:
    """Decorator that registers a Source class by its ``name`` attribute.

    Usage::

        @register
        class MySource:
            name = "mysource"
            ...

    The source is now accessible as ``SOURCES["mysource"]``.
    """
    SOURCES[cls.name] = cls
    return cls


# ---------------------------------------------------------------------------
# Source protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Source(Protocol):
    """Protocol every EdgeDash data source must satisfy.

    A source has exactly one goal: fetch listings from one external origin
    and return them as a list of normalised dicts.  All parsing, filtering,
    and field mapping live inside the source class — never in the Fetcher.

    Stop condition: ``fetch`` returns when it has exhausted the pages it
    was configured to fetch, or when no more matching results are found.
    It always returns a list (possibly empty) rather than raising.
    """

    name: str

    def fetch(self, config: "Config") -> list[dict]:
        """Fetch listings from this source and return normalised rows.

        Args:
            config: Loaded user configuration used for filtering.

        Returns:
            A list of normalised dicts with EXACTLY these keys:
            - source (str)         : canonical source name
            - external_id (str)    : stable unique ID from the source
            - title (str | None)   : job title
            - company (str | None) : company name
            - location (str | None): location string
            - url (str)            : canonical URL to the listing
            - description (str | None): full job description text
            - posted_at (str | None)  : ISO date string or None
            - raw (dict)           : the raw payload exactly as received

            Missing values must be None — never empty string or "N/A".
        """
        ...