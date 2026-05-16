"""MyCareersFuture.sg adapter.

Uses the undocumented v2 search endpoint verified 2026-04-11:

    POST https://api.mycareersfuture.gov.sg/v2/search?limit=30&page=0
    Body: {"search": "<keyword>", "sortBy": ["new_posting_date"], ...}

The `schemes` field is empty on most listings so the Singaporeans/PR-only
filter has to go through LLM extraction of the description text. Position
levels and minimum years of experience DO filter server-side reliably.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from ..config import MCFSourceConfig
from ..models import JobPost
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://api.mycareersfuture.gov.sg/v2/search"
DETAIL = "https://api.mycareersfuture.gov.sg/v2/jobs"
PAGE_SIZE = 30


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|ul|ol|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


class MyCareersFutureScraper(Scraper):
    name = "mycareersfuture"

    def __init__(self, cfg: MCFSourceConfig) -> None:
        self.cfg = cfg
        self.client = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": "jd-search/0.1", "Content-Type": "application/json"},
        )

    def _fetch_detail(self, uuid: str) -> dict[str, Any]:
        """GET /v2/jobs/{uuid} — the search endpoint omits `description`,
        so we need this follow-up call to get the actual JD body and the
        richer fields (schemes, minimumYearsExperience, otherRequirements)."""
        try:
            r = self.client.get(f"{DETAIL}/{uuid}")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning("MCF detail %s failed: %s", uuid, exc)
            return {}

    def fetch(self) -> list[JobPost]:
        seen_ids: set[str] = set()
        out: list[JobPost] = []

        for keyword in self.cfg.keywords:
            for page in range(self.cfg.max_pages):
                body: dict[str, Any] = {
                    "search": keyword,
                    "sortBy": ["new_posting_date"],
                }
                if self.cfg.position_levels:
                    body["positionLevels"] = self.cfg.position_levels
                if self.cfg.min_years_experience:
                    body["minimumYearsExperience"] = self.cfg.min_years_experience

                try:
                    r = self.client.post(
                        BASE, params={"limit": PAGE_SIZE, "page": page}, json=body
                    )
                    r.raise_for_status()
                    data = r.json()
                except Exception as exc:
                    log.warning("MCF %s page=%d failed: %s", keyword, page, exc)
                    break

                results = data.get("results") or []
                if not results:
                    break

                for j in results:
                    uuid = j.get("uuid") or ""
                    if uuid in seen_ids:
                        continue
                    seen_ids.add(uuid)

                    # The v2/search endpoint returns metadata only — no
                    # description. Pull the full posting from v2/jobs/{uuid}
                    # and prefer its fields where available.
                    detail = self._fetch_detail(uuid) if uuid else {}
                    src = detail or j

                    title = (src.get("title") or j.get("title") or "").strip()
                    company = (
                        (src.get("postedCompany") or j.get("postedCompany") or {})
                        .get("name", "")
                        or ""
                    )
                    description = _strip_html(
                        src.get("description") or j.get("description") or ""
                    )
                    url = f"https://www.mycareersfuture.gov.sg/job/{uuid}" if uuid else ""

                    # Keep structured extras on the JobPost.raw for later introspection
                    raw = {
                        "uuid": uuid,
                        "positionLevels": [
                            p.get("position") for p in src.get("positionLevels") or []
                        ],
                        "minimumYearsExperience": src.get("minimumYearsExperience")
                        or j.get("minimumYearsExperience"),
                        "categories": [c.get("category") for c in src.get("categories") or []],
                        "schemes": src.get("schemes") or j.get("schemes") or [],
                        "otherRequirements": src.get("otherRequirements")
                        or j.get("otherRequirements"),
                    }

                    out.append(
                        JobPost(
                            source=self.name,
                            source_id=uuid,
                            title=title,
                            company=company or "Unknown",
                            location="Singapore",
                            url=url,
                            description=description,
                            posted_on=j.get("metadata", {}).get("newPostingDate")
                            if isinstance(j.get("metadata"), dict)
                            else None,
                            raw=raw,
                        )
                    )

                # Stop early if fewer than a full page came back
                if len(results) < PAGE_SIZE:
                    break

        log.info("MCF fetched %d postings across %d keywords", len(out), len(self.cfg.keywords))
        return out
