"""Workday CXS adapter.

Verified 2026-04-11 against Unilever:

    POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

Important gotchas:

- `searchText` searches free text, NOT location — so we filter client-side
  on `locationsText` containing "Singapore".
- Rate limits are aggressive: 1 req/sec, realistic User-Agent required.
- Each tenant's `(tenant, wd_shard, wd_site)` triple must match exactly.
  The `verify-workday` CLI helper is the easiest way to get them right.
- `total` tells you the full size; paginate with offset.
- The list payload does NOT include descriptions; we fetch the detail
  endpoint per SG match.
"""

from __future__ import annotations

import html
import logging
import re
import time

import httpx

from ..config import CompanyEntry, WorkdaySourceConfig, title_is_relevant
from ..models import JobPost
from .base import Scraper

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 jd-search/0.1"
)


def _plain(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|ul|ol|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def cxs_base(company: CompanyEntry) -> str:
    return (
        f"https://{company.tenant}.{company.wd_shard}.myworkdayjobs.com"
        f"/wday/cxs/{company.tenant}/{company.wd_site}"
    )


class WorkdayScraper(Scraper):
    name = "workday"

    def __init__(
        self,
        cfg: WorkdaySourceConfig,
        companies: list[CompanyEntry],
        title_keywords: list[str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.title_keywords = title_keywords or []
        self.companies = [
            c
            for c in companies
            if c.ats == "workday" and c.verified and c.tenant and c.wd_shard and c.wd_site
        ]
        self.client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def fetch(self) -> list[JobPost]:
        out: list[JobPost] = []
        for company in self.companies:
            try:
                out.extend(self._fetch_one(company))
            except Exception as exc:
                log.warning("Workday %s failed: %s", company.name, exc)
        log.info("Workday fetched %d postings across %d companies", len(out), len(self.companies))
        return out

    def _fetch_one(self, company: CompanyEntry) -> list[JobPost]:
        base = cxs_base(company)
        jobs_url = f"{base}/jobs"
        # Workday CXS caps per-request limit at 20; our config's pagination_limit
        # controls max_pages indirectly via the total we sweep.
        limit = 20
        offset = 0
        sg_matches: list[dict] = []

        for _ in range(self.cfg.max_pages):
            body = {"appliedFacets": {}, "limit": limit, "offset": offset}
            r = self.client.post(jobs_url, json=body)
            if r.status_code != 200:
                log.warning(
                    "Workday %s POST %s → HTTP %d; check tenant/site path",
                    company.name,
                    jobs_url,
                    r.status_code,
                )
                break
            data = r.json()
            postings = data.get("jobPostings", []) or []
            for p in postings:
                if "Singapore" not in (p.get("locationsText") or ""):
                    continue
                if self.title_keywords and not title_is_relevant(p.get("title", ""), self.title_keywords):
                    continue
                sg_matches.append(p)

            total = data.get("total") or 0
            offset += limit
            if offset >= total or not postings:
                break
            # Stop early if we've seen enough pages — limits cost
            if offset >= self.cfg.pagination_limit:
                break
            time.sleep(self.cfg.request_delay_seconds)

        # Fetch detail for each SG hit
        result: list[JobPost] = []
        for p in sg_matches:
            external_path = p.get("externalPath") or ""
            detail_url = f"{base}/job{external_path}"
            title = p.get("title") or ""
            location = p.get("locationsText") or "Singapore"
            description = ""
            try:
                dr = self.client.get(detail_url)
                if dr.status_code == 200:
                    detail = dr.json().get("jobPostingInfo", {}) or {}
                    description = _plain(detail.get("jobDescription"))
            except Exception as exc:
                log.debug("Workday detail fetch failed for %s: %s", external_path, exc)

            public_url = (
                f"https://{company.tenant}.{company.wd_shard}.myworkdayjobs.com"
                f"/{company.wd_site}{external_path}"
            )

            result.append(
                JobPost(
                    source=f"{self.name}:{company.name.lower()}",
                    source_id=(p.get("bulletFields") or [""])[0] or external_path,
                    title=title,
                    company=company.name,
                    location=location,
                    url=public_url,
                    description=description or title,
                    posted_on=p.get("postedOn"),
                    raw={"posting": p},
                )
            )
            time.sleep(self.cfg.request_delay_seconds)

        return result
