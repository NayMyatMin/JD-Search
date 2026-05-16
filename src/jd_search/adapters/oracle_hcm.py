"""Oracle HCM Candidate Experience adapter.

Verified 2026-04-11 against Honeywell (`ibqbjb.fa.ocs.oraclecloud.com`,
siteNumber=Honeywell) → 12 Singapore jobs.

The `recruitingCEJobRequisitions` endpoint is the public feed that powers
the candidate-experience UI. We filter server-side with
`finder=findReqs;siteNumber=<site>,keyword=Singapore` and fetch details
per requisition.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from ..config import CompanyEntry, OracleHCMSourceConfig, title_is_relevant
from ..models import JobPost
from .base import Scraper

log = logging.getLogger(__name__)


def _plain(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|ul|ol|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


class OracleHCMScraper(Scraper):
    name = "oracle_hcm"

    def __init__(
        self,
        cfg: OracleHCMSourceConfig,
        companies: list[CompanyEntry],
        title_keywords: list[str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.title_keywords = title_keywords or []
        self.companies = [
            c
            for c in companies
            if c.ats == "oracle_hcm" and c.verified and c.oracle_pod and c.oracle_site
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
                log.warning("Oracle HCM %s failed: %s", company.name, exc)
        log.info("Oracle HCM fetched %d postings across %d companies", len(out), len(self.companies))
        return out

    def _fetch_one(self, company: CompanyEntry) -> list[JobPost]:
        base = f"https://{company.oracle_pod}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        offset = 0
        all_items: list[dict] = []

        while True:
            params = {
                "finder": f"findReqs;siteNumber={company.oracle_site},keyword={self.cfg.keyword}",
                "limit": self.cfg.pagination_limit,
                "offset": offset,
                "expand": "all",
            }
            r = self.client.get(base, params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            if not items:
                break

            # The Oracle HCM payload has a single wrapper item per page whose
            # `requisitionList` holds the actual job rows.
            wrapper = items[0] if items else {}
            reqs = wrapper.get("requisitionList") or []
            total = wrapper.get("TotalJobsCount") or 0
            all_items.extend(reqs)

            offset += self.cfg.pagination_limit
            if offset >= total or not reqs:
                break

        result: list[JobPost] = []
        for it in all_items:
            loc = it.get("PrimaryLocation") or ""
            if "Singapore" not in loc:
                continue
            title = it.get("Title") or ""
            if self.title_keywords and not title_is_relevant(title, self.title_keywords):
                continue
            apply_url = it.get("ExternalApplyUrl") or it.get("ExternalContentUrl") or ""
            desc = _plain(it.get("ExternalDescriptionStr"))
            result.append(
                JobPost(
                    source=f"{self.name}:{company.name.lower()}",
                    source_id=str(it.get("Id") or it.get("PostingId") or ""),
                    title=title,
                    company=company.name,
                    location=loc,
                    url=apply_url or (company.careers_url or ""),
                    description=desc,
                    posted_on=it.get("PostedDate"),
                    raw={"item": it},
                )
            )
        return result
