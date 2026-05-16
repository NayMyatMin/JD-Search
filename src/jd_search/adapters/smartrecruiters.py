"""SmartRecruiters Posting API adapter.

Verified 2026-04-11 against LVMH2 → 55 Singapore jobs.

Two-call pattern:

    GET /v1/companies/{slug}/postings?country=sg&limit=100&offset=0
        → list of summaries (no description)
    GET /v1/companies/{slug}/postings/{id}
        → full description

We fetch all matching SG postings for every verified SmartRecruiters
company in companies.yaml.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from ..config import CompanyEntry, SmartRecruitersSourceConfig, title_is_relevant
from ..models import JobPost
from .base import Scraper

log = logging.getLogger(__name__)

BASE = "https://api.smartrecruiters.com/v1/companies"


def _plain(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|ul|ol|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


class SmartRecruitersScraper(Scraper):
    name = "smartrecruiters"

    def __init__(
        self,
        cfg: SmartRecruitersSourceConfig,
        companies: list[CompanyEntry],
        title_keywords: list[str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.title_keywords = title_keywords or []
        self.companies = [
            c for c in companies if c.ats == "smartrecruiters" and c.verified and c.tenant
        ]
        self.client = httpx.Client(
            timeout=30.0, headers={"User-Agent": "jd-search/0.1", "Accept": "application/json"}
        )

    def fetch(self) -> list[JobPost]:
        out: list[JobPost] = []
        for company in self.companies:
            try:
                out.extend(self._fetch_one(company))
            except Exception as exc:
                log.warning("SmartRecruiters %s failed: %s", company.name, exc)
        log.info(
            "SmartRecruiters fetched %d postings across %d companies",
            len(out),
            len(self.companies),
        )
        return out

    def _fetch_one(self, company: CompanyEntry) -> list[JobPost]:
        slug = company.tenant or ""
        offset = 0
        postings_meta: list[dict] = []

        while True:
            r = self.client.get(
                f"{BASE}/{slug}/postings",
                params={
                    "country": self.cfg.country,
                    "limit": self.cfg.pagination_limit,
                    "offset": offset,
                },
            )
            r.raise_for_status()
            data = r.json()
            batch = data.get("content", [])
            postings_meta.extend(batch)
            total = data.get("totalFound", 0)
            offset += len(batch)
            if offset >= total or not batch:
                break

        # Title prefilter — skip irrelevant roles before paying for detail fetches
        if self.title_keywords:
            postings_meta = [
                p for p in postings_meta if title_is_relevant(p.get("name", ""), self.title_keywords)
            ]

        # Second pass: fetch full description for each
        result: list[JobPost] = []
        for p in postings_meta:
            pid = p.get("id")
            if not pid:
                continue
            try:
                r = self.client.get(f"{BASE}/{slug}/postings/{pid}")
                r.raise_for_status()
                detail = r.json()
            except Exception as exc:
                log.debug("SR detail fetch failed for %s: %s", pid, exc)
                detail = {}

            sections = detail.get("jobAd", {}).get("sections", {}) if detail else {}
            description = "\n\n".join(
                _plain((sections.get(k) or {}).get("text"))
                for k in ("jobDescription", "qualifications", "additionalInformation")
                if sections.get(k)
            ).strip()

            location = p.get("location") or {}
            if not isinstance(location, dict):
                location = {}
            loc_str = ", ".join(
                x for x in (location.get("city"), location.get("region"), location.get("country")) if x
            )

            # SmartRecruiters sometimes returns a list of URL objects, sometimes a single string.
            url = detail.get("postingUrl") or detail.get("applyUrl") or p.get("postingUrl") or ""
            if isinstance(url, list) and url:
                url = url[0] if isinstance(url[0], str) else ""

            result.append(
                JobPost(
                    source=f"{self.name}:{company.name.lower()}",
                    source_id=pid,
                    title=p.get("name", ""),
                    company=company.name,
                    location=loc_str or "Singapore",
                    url=url or "",
                    description=description or _plain(p.get("name", "")),
                    posted_on=p.get("releasedDate"),
                    raw={"summary": p, "industry": p.get("industry")},
                )
            )
        return result
