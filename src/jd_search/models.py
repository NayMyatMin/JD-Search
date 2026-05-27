"""Pydantic models for the pipeline.

Stages and what they produce:

    adapters   →  JobPost           (raw, source-specific)
    extract    →  ExtractedJD       (+ Dealbreakers) via GPT-5-mini
    gate       →  GateResult        (kept | rejected | borderline)
    score      →  JobScore          via GPT-5-mini rubric
    persist    →  StoredJob         (everything stitched, written to sqlite)
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stage 1 — raw postings from adapters
# ---------------------------------------------------------------------------


class JobPost(BaseModel):
    """Normalized posting from any source adapter."""

    source: str                         # e.g., "mycareersfuture", "workday:unilever"
    source_id: str | None = None        # native id from the source, if any
    title: str
    company: str
    location: str
    url: str
    description: str                    # plain text or HTML — extract.py will normalize
    posted_on: str | None = None        # source-native string; parsed lazily
    raw: dict | None = None             # original payload, for debugging

    def content_hash(self) -> str:
        """Stable dedup key across sources for the same underlying posting."""
        normalized_title = " ".join(self.title.lower().split())
        normalized_company = " ".join(self.company.lower().split())
        body_head = " ".join(self.description.split())[:500].lower()
        h = hashlib.sha256()
        h.update(f"{normalized_title}|{normalized_company}|{body_head}".encode())
        return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage 2 — LLM-extracted structured fields
# ---------------------------------------------------------------------------


class Dealbreakers(BaseModel):
    """Hard constraints checked in the gate step."""

    citizens_pr_only: bool = Field(
        description="True if the JD requires Singaporean citizens or PRs only (sponsorship unavailable)."
    )
    citizens_pr_evidence: str | None = Field(
        default=None, description="Quoted phrase from the JD, if citizens_pr_only is True."
    )

    requires_non_english_language: bool = Field(
        description="True if the JD requires a non-English language as a hard requirement (not preferred/nice-to-have)."
    )
    non_english_languages: list[str] = Field(
        default_factory=list, description="Languages required besides English."
    )
    language_evidence: str | None = None

    sponsorship_available: bool | None = Field(
        default=None, description="True if JD explicitly says sponsorship is available, False if explicitly not, None if unclear."
    )

    seniority_fit: Literal["too_junior", "target", "too_senior", "unclear"] = "unclear"
    ambiguous_citizenship: bool = Field(
        default=False,
        description="True if the JD hints at SG-preference without stating it outright — use for borderline warnings.",
    )


class ExtractedJD(BaseModel):
    """Structured view of a posting produced by GPT-5-mini."""

    title_clean: str
    seniority: Literal[
        "intern",
        "entry",
        "junior",
        "mid",
        "senior",
        "lead",
        "manager",
        "senior_manager",
        "director",
        "vp",
        "unclear",
    ]
    years_experience_min: int | None = None
    years_experience_max: int | None = None

    role_family: str                    # e.g., "Brand Marketing", "Product Marketing"
    sectors: list[str] = Field(default_factory=list)
    geographic_scope: list[str] = Field(default_factory=list)

    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)

    dealbreakers: Dealbreakers

    summary: str = Field(description="≤40 words plain-English summary of the role.")


# ---------------------------------------------------------------------------
# Stage 3 — gate result
# ---------------------------------------------------------------------------


class GateResult(BaseModel):
    verdict: Literal["keep", "reject", "borderline"]
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 4 — scoring
# ---------------------------------------------------------------------------


class JobScore(BaseModel):
    score: int = Field(ge=0, le=100)
    anchor_similarity: int = Field(ge=0, le=100, description="Fit vs the four reference JDs")
    resume_fit: int = Field(ge=0, le=100, description="Fit vs the candidate resume")
    strengths: list[str] = Field(default_factory=list, max_length=3)
    gaps: list[str] = Field(default_factory=list, max_length=3)
    explanation: str = Field(description="≤40 word human-readable summary")


# ---------------------------------------------------------------------------
# Stage 4.5 — canonical apply-link lookup (top matches only)
# ---------------------------------------------------------------------------


class ApplyCandidate(BaseModel):
    """One URL returned by the web search step, before validation."""

    url: str
    source: Literal["linkedin", "company", "other"]
    matched_title: str = Field(description="Title text the search engine surfaced for this URL.")


class ApplyLinkSearchResult(BaseModel):
    """Structured response from the web-search LLM call."""

    candidates: list[ApplyCandidate] = Field(default_factory=list, max_length=4)


class ApplyLink(BaseModel):
    """A validated apply URL for a top-match posting."""

    url: str
    source: Literal["linkedin", "company", "other"]
    matched_title: str
    confidence: int = Field(ge=0, le=100, description="Fuzzy title match score (0–100).")
    verified_at: datetime


class ApplyLinks(BaseModel):
    """Container for per-job apply links, since we may surface multiple sources."""

    linkedin: ApplyLink | None = None
    company: ApplyLink | None = None
    other: ApplyLink | None = None


# ---------------------------------------------------------------------------
# Stage 5 — persisted record
# ---------------------------------------------------------------------------


class StoredJob(BaseModel):
    id: str                             # content_hash
    source: str
    source_id: str | None = None
    title: str
    company: str
    location: str
    url: str
    description: str
    posted_on: str | None = None

    first_seen_at: datetime
    last_seen_at: datetime

    extracted: ExtractedJD | None = None
    gate: GateResult | None = None
    score: JobScore | None = None
    apply_links: ApplyLinks | None = None
    seen_in_digest_on: date | None = None
    dismissed: bool = False

    scoring_version: int = 1
