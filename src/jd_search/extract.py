"""GPT-5-mini structured extraction of raw job postings.

Given a raw JobPost, produce an ExtractedJD (with Dealbreakers) via
`client.responses.parse()` with a Pydantic schema. This is the single
most important LLM call in the pipeline — the gate depends on its
accuracy for citizenship and language flags.
"""

from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI
from openai.types.responses import ResponseOutputItem  # noqa: F401  (type only)

from .config import ScoringConfig
from .models import ExtractedJD, JobPost

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You extract structured fields from Singapore job postings.

Your most important task is identifying two hard dealbreakers:

1. **citizens_pr_only** — set TRUE if the JD states or strongly implies that
   only Singaporean citizens or Permanent Residents will be considered, OR
   that no work pass / employment pass sponsorship is available.
   Common phrasings:
     - "Singaporeans / PRs only"
     - "Only Singaporean citizens and PRs need apply"
     - "Due to business requirements, only local candidates can be considered"
     - "No sponsorship available"
     - "Must be eligible to work in Singapore without sponsorship"
   If the phrasing is softer ("preference for local candidates", "Singaporeans
   encouraged to apply", ambiguous work-pass language), set citizens_pr_only
   to FALSE but set ambiguous_citizenship to TRUE and note the phrase.

2. **requires_non_english_language** — set TRUE only if a non-English language
   is a HARD requirement. List the languages in non_english_languages.
   Soft phrasing ("Mandarin is a plus", "bilingual preferred") does NOT count.
   Hard phrasing ("must be fluent in Mandarin to liaise with China team",
   "proficient in Bahasa Melayu required") DOES count.

Also extract: clean title, seniority level, years of experience range
(min/max), role_family (e.g., "Brand Marketing", "Product Marketing"),
sectors, geographic scope, must-have and nice-to-have skills, and a
≤40-word summary.

Be strict about evidence: if you mark citizens_pr_only or
requires_non_english_language as TRUE, you MUST quote the exact phrase
from the JD in the evidence field."""


def _user_prompt(job: JobPost) -> str:
    return f"""JOB POSTING
===
Title: {job.title}
Company: {job.company}
Location: {job.location}
Source: {job.source}

Description:
{job.description[:8000]}
"""


class Extractor:
    """Wraps GPT-5-mini structured output calls."""

    def __init__(self, cfg: ScoringConfig, api_key: str) -> None:
        self.cfg = cfg
        self.client = OpenAI(api_key=api_key)

    def extract(self, job: JobPost) -> ExtractedJD | None:
        try:
            resp = self.client.responses.parse(
                model=self.cfg.model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _user_prompt(job)},
                ],
                text_format=ExtractedJD,
                reasoning={"effort": self.cfg.reasoning_effort},
            )
        except Exception as exc:
            log.warning("extract failed for %s / %s: %s", job.company, job.title, exc)
            return None

        # responses.parse exposes the typed output on `output_parsed`
        parsed: Any = getattr(resp, "output_parsed", None)
        if isinstance(parsed, ExtractedJD):
            return parsed
        # Fallback: some SDK versions return a list with a parsed field
        try:
            return ExtractedJD.model_validate(parsed)
        except Exception as exc:
            log.warning("extract parse failed for %s: %s", job.title, exc)
            return None
