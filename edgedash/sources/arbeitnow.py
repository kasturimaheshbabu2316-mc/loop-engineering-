"""ArbeitnowSource — free public job board API, no key required.

API docs: https://www.arbeitnow.com/api/job-board-api

Response shape (page 1):
  {
    "data": [ { "slug", "company_name", "title", "description",
                "location", "url", "remote", "tags", "created_at",
                "job_types" }, ... ],
    "meta": { "current_page", ... },
    "links": { "next": "...?page=2" | null }
  }

Paging strategy:
  - Fetch page 1, check for keyword matches.
  - Continue paging while results keep matching config.keywords,
    up to a hard cap of MAX_PAGES pages.
  - Rate-limit: 1 second between page requests.

Filtering:
  - Primary: keyword match AND city match (case-insensitive substring).
  - Fallback: if fewer than MIN_RESULTS survive the city filter,
    relax to keyword-only and log a warning.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config
from edgedash.sources.base import Source, register
from edgedash.sources.http import SourceError, get_json

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 5
_MIN_RESULTS = 5           # relax city filter below this threshold
_PAGE_SLEEP = 1.0          # seconds between requests (rate limiting)


def _strip_html(text: str | None) -> str | None:
    """Remove HTML tags from a string; return None if the result is empty."""
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_created_at(unix_ts: int | None) -> str | None:
    """Convert a Unix timestamp to an ISO 8601 date string."""
    if unix_ts is None:
        return None
    try:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).date().isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _keyword_match(listing: dict[str, Any], keywords: list[str]) -> bool:
    """Return True if any keyword appears in title or description."""
    searchable = " ".join(filter(None, [
        listing.get("title", "") or "",
        listing.get("description", "") or "",
    ])).lower()
    return any(kw.lower() in searchable for kw in keywords)


def _city_match(listing: dict[str, Any], city: str) -> bool:
    """Return True if the listing location contains the target city."""
    location = (listing.get("location") or "").lower()
    return city.lower() in location


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Arbeitnow listing to the EdgeDash normalised schema."""
    return {
        "source":      "arbeitnow",
        "external_id": raw.get("slug") or None,
        "title":       raw.get("title") or None,
        "company":     raw.get("company_name") or None,
        "location":    raw.get("location") or None,
        "url":         raw.get("url") or None,
        "description": _strip_html(raw.get("description")),
        "posted_at":   _parse_created_at(raw.get("created_at")),
        "raw":         raw,
    }


@register
class ArbeitnowSource:
    """Fetches job listings from the free Arbeitnow job board API.

    No API key or sign-up required.
    Stop condition: no more pages, no more keyword matches, or MAX_PAGES hit.
    """

    name: str = "arbeitnow"

    def fetch(self, config: Config) -> list[dict]:
        """Fetch and filter listings from Arbeitnow.

        Args:
            config: User configuration (keywords and target_city used).

        Returns:
            List of normalised dicts ready for storage.
        """
        keywords = config.keywords
        city = config.target_city

        all_raw: list[dict] = []
        page = 1

        while page <= _MAX_PAGES:
            try:
                data = get_json(_BASE_URL, params={"page": page})
            except SourceError as exc:
                logger.error("Arbeitnow page %d failed: %s", page, exc)
                break

            items: list[dict] = data.get("data", [])
            if not items:
                logger.info("Arbeitnow: no more results at page %d.", page)
                break

            # Collect keyword-matching results from this page
            matched_this_page = [i for i in items if _keyword_match(i, keywords)]

            if not matched_this_page and page > 1:
                # No matches on this page — stop paging
                logger.info(
                    "Arbeitnow: no keyword matches on page %d — stopping.", page
                )
                break

            all_raw.extend(matched_this_page)
            logger.info(
                "Arbeitnow page %d: %d total, %d keyword-matched.",
                page, len(items), len(matched_this_page),
            )

            # Check if there is a next page
            next_url = data.get("links", {}).get("next")
            if not next_url:
                logger.info("Arbeitnow: reached last page (%d).", page)
                break

            page += 1
            if page <= _MAX_PAGES:
                time.sleep(_PAGE_SLEEP)

        total_raw = len(all_raw)
        logger.info("Arbeitnow: %d listings collected before location filter.", total_raw)

        # Apply city filter
        city_filtered = [r for r in all_raw if _city_match(r, city)]

        if len(city_filtered) < _MIN_RESULTS and len(city_filtered) < total_raw:
            print(
                f"  [ArbeitnowSource] City filter '{city}' left only "
                f"{len(city_filtered)}/{total_raw} results — relaxing to "
                f"keyword-only. You may see remote or nearby roles."
            )
            final_raw = all_raw
        else:
            final_raw = city_filtered

        print(
            f"  [ArbeitnowSource] {total_raw} raw results fetched, "
            f"{len(final_raw)} survived filtering "
            f"(city='{city}', keywords={keywords[:3]}…)."
        )

        return [_normalise(r) for r in final_raw]