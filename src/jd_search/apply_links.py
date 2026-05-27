"""Find canonical apply URLs for top-tier matches.

After scoring, this stage takes top-matched JobPosts and:

1. Uses OpenAI Responses API + the built-in web_search tool to find candidate
   URLs (preferring LinkedIn and the company's own ATS; excluding aggregators).
2. Validates each candidate by fetching the page and fuzzy-matching the page's
   title against the JD title — except LinkedIn, which blocks bots, so we accept
   linkedin.com/jobs/view/<id> URLs based on the search snippet alone.
3. Returns an ApplyLinks bundle (linkedin + company, either may be None) so the
   digest can surface both apply paths to the user.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
from rapidfuzz import fuzz

from .config import ApplyLinksConfig
from .models import (
    ApplyCandidate,
    ApplyLink,
    ApplyLinkSearchResult,
    ApplyLinks,
    JobPost,
)

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You find canonical apply URLs for Singapore job postings.

Given a job title and company, search the web and return up to 4 candidate URLs:
- The LinkedIn job posting (URL pattern: linkedin.com/jobs/view/<numeric-id>)
- The company's own careers/ATS page for THIS specific role (URL on the
  company's own domain, or on greenhouse.io, lever.co, ashbyhq.com, workday
  (*.myworkdayjobs.com), smartrecruiters.com, oracle (*.oraclecloud.com),
  successfactors.com, taleo.net, icims.com)

EXCLUDE aggregators and government boards:
- mycareersfuture.gov.sg
- indeed.com
- glassdoor.com
- jora.com
- foundit.sg / foundit.com
- jobstreet.com / jobstreet.sg
- reed.co.uk
- ziprecruiter.com
- simplyhired.com
- linkedin.com/jobs/search (only /jobs/view/<id> is canonical)

For each candidate URL, classify "source" as exactly one of:
- "linkedin": URL is on linkedin.com/jobs/view/...
- "company": URL is on the company's own domain or a known ATS for the company
- "other": anything else that still looks legitimate and direct-apply

Set "matched_title" to the title text the search engine actually surfaced for
that URL (used for verification). Be honest — do NOT invent a title that wasn't
in the search results.

If you cannot find any confident match, return an empty candidates list."""


# Patterns we recognise as "definitely LinkedIn job page" (used for the
# bot-blocking shortcut — LinkedIn returns HTTP 999 to non-browser fetches).
_LINKEDIN_JOB_RE = re.compile(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/jobs/view/\d+")

# Aggregator hosts we sanity-check at validation time — if the LLM slips one
# through despite the prompt, drop it on the floor.
_AGGREGATOR_HOSTS = (
    "mycareersfuture.gov.sg",
    "indeed.com",
    "glassdoor.com",
    "jora.com",
    "foundit.sg",
    "foundit.com",
    "jobstreet.com",
    "jobstreet.sg",
    "reed.co.uk",
    "ziprecruiter.com",
    "simplyhired.com",
)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def normalize_title(title: str) -> str:
    """Strip noise so fuzzy match focuses on the role, not packaging.

    Removes:
    - leading numeric job IDs (e.g. "26944441 Regional Marketing...")
    - parentheticals  (e.g. "Marketing Manager (Brand & Growth)" → "Marketing Manager")
    - common date/contract noise (e.g. "-NEW-", "6-9 Months Contract")
    - "Senior / " or "Junior / " prefix forks
    Lowercases and collapses whitespace.
    """
    t = title or ""
    t = re.sub(r"^\s*\d{6,}\s+", "", t)             # leading job IDs
    t = re.sub(r"\([^)]*\)", " ", t)                # parentheticals
    t = re.sub(r"-\s*NEW\s*-", " ", t, flags=re.I)  # "-NEW-" tag
    t = re.sub(
        r"\b\d+\s*(?:[-–]\s*\d*\s*)?month[s]?\s*(?:contract)?\b", " ", t, flags=re.I
    )
    t = re.sub(r"^\s*(senior|junior)\s*/\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def fuzzy_title_match(jd_title: str, candidate_text: str) -> int:
    """Return a 0–100 confidence score that `candidate_text` describes the same
    role as `jd_title`. Uses rapidfuzz token_set_ratio on normalized strings —
    token_set ignores token repetition and extra tokens, which matters because
    company career pages typically contain the role title in BOTH <title> and
    <h1>, doubling the token count vs the JD title."""
    a = normalize_title(jd_title)
    b = normalize_title(candidate_text)
    if not a or not b:
        return 0
    return int(fuzz.token_set_ratio(a, b))


def _is_aggregator(url: str) -> bool:
    u = url.lower()
    return any(host in u for host in _AGGREGATOR_HOSTS)


class ApplyLinkFinder:
    """Wraps the web-search-then-validate flow for one job at a time."""

    def __init__(self, cfg: ApplyLinksConfig, api_key: str) -> None:
        self.cfg = cfg
        self.client = OpenAI(api_key=api_key)
        self._http = httpx.Client(
            timeout=cfg.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html"},
        )

    def find(self, job: JobPost) -> ApplyLinks:
        candidates = self._search(job)
        out = ApplyLinks()
        for cand in candidates:
            if _is_aggregator(cand.url):
                log.debug("apply-links: dropping aggregator %s", cand.url)
                continue
            validated = self._validate(cand, job.title)
            if not validated:
                continue
            slot: str = cand.source if cand.source in {"linkedin", "company"} else "other"
            existing = getattr(out, slot, None)
            if existing is None or validated.confidence > existing.confidence:
                setattr(out, slot, validated)
        return out

    # ------------------------------------------------------------------
    # 1) web search
    # ------------------------------------------------------------------

    def _search(self, job: JobPost) -> list[ApplyCandidate]:
        user = (
            f"Job title: {job.title}\n"
            f"Company: {job.company}\n"
            f"Location: Singapore\n\n"
            "Find canonical apply URLs (LinkedIn job page and the company's "
            "own careers/ATS page for this specific role)."
        )
        try:
            resp = self.client.responses.parse(
                model=self.cfg.search_model,
                tools=[{"type": "web_search"}],
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                text_format=ApplyLinkSearchResult,
            )
        except Exception as exc:
            log.warning(
                "apply-links search failed for %s / %s: %s", job.company, job.title, exc
            )
            return []

        parsed: Any = getattr(resp, "output_parsed", None)
        if isinstance(parsed, ApplyLinkSearchResult):
            return parsed.candidates
        try:
            return ApplyLinkSearchResult.model_validate(parsed).candidates
        except Exception as exc:
            log.warning("apply-links parse failed for %s: %s", job.title, exc)
            return []

    # ------------------------------------------------------------------
    # 2) validate one candidate
    # ------------------------------------------------------------------

    def _validate(self, cand: ApplyCandidate, jd_title: str) -> ApplyLink | None:
        # LinkedIn: bots get HTTP 999. Trust the search snippet's title.
        if _LINKEDIN_JOB_RE.match(cand.url):
            conf = fuzzy_title_match(jd_title, cand.matched_title)
            if conf >= self.cfg.min_match_confidence:
                return ApplyLink(
                    url=cand.url,
                    source="linkedin",
                    matched_title=cand.matched_title,
                    confidence=conf,
                    verified_at=datetime.utcnow(),
                )
            log.debug(
                "apply-links: linkedin candidate rejected (conf=%d): %s vs %s",
                conf, jd_title, cand.matched_title,
            )
            return None

        # Other URLs: fetch and pull title/h1.
        try:
            r = self._http.get(cand.url)
        except Exception as exc:
            log.debug("apply-links: fetch failed %s: %s", cand.url, exc)
            return None
        if r.status_code >= 400:
            log.debug("apply-links: %s returned HTTP %d", cand.url, r.status_code)
            return None

        try:
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as exc:
            log.debug("apply-links: BS4 parse failed for %s: %s", cand.url, exc)
            return None
        page_title = (soup.title.string or "").strip() if soup.title else ""
        h1 = soup.find("h1")
        h1_text = h1.get_text(" ", strip=True) if h1 else ""
        combined = f"{page_title} {h1_text}".strip()
        if not combined:
            return None

        conf = fuzzy_title_match(jd_title, combined)
        if conf >= self.cfg.min_match_confidence:
            return ApplyLink(
                url=cand.url,
                source=cand.source if cand.source in {"linkedin", "company"} else "other",
                matched_title=combined[:200],
                confidence=conf,
                verified_at=datetime.utcnow(),
            )
        log.debug(
            "apply-links: candidate rejected (conf=%d): %s vs page='%s'",
            conf, jd_title, combined[:120],
        )
        return None

    def close(self) -> None:
        self._http.close()
