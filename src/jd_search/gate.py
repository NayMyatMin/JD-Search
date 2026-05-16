"""Deterministic hard-filter gate.

Runs AFTER extraction. Takes ExtractedJD (which contains LLM-derived
Dealbreakers) and makes a keep / borderline / reject decision using ONLY
the config rules — no LLM calls here. This keeps the dealbreaker logic
auditable and cheap.
"""

from __future__ import annotations

import re

from .config import GateConfig
from .models import ExtractedJD, GateResult, JobPost


def _match_reject_title(patterns: list[str], title: str) -> str | None:
    t = title.lower()
    for p in patterns:
        if re.search(p, t):
            return p
    return None


def _language_blocked(
    extracted: ExtractedJD, reject_list: list[str]
) -> tuple[bool, list[str]]:
    if not extracted.dealbreakers.requires_non_english_language:
        return False, []
    blocked: list[str] = []
    reject_lc = [r.lower() for r in reject_list]
    for lang in extracted.dealbreakers.non_english_languages:
        if any(r in lang.lower() or lang.lower() in r for r in reject_lc):
            blocked.append(lang)
    return bool(blocked), blocked


def _seniority_mismatch(
    extracted: ExtractedJD, min_y: int, max_y: int
) -> str | None:
    if extracted.dealbreakers.seniority_fit in ("too_junior", "too_senior"):
        return extracted.dealbreakers.seniority_fit
    ymin = extracted.years_experience_min
    ymax = extracted.years_experience_max
    if ymin is not None and ymin > max_y:
        return "too_senior"
    if ymax is not None and ymax < min_y:
        return "too_junior"
    return None


def run_gate(
    job: JobPost,
    extracted: ExtractedJD,
    cfg: GateConfig,
) -> GateResult:
    reasons: list[str] = []
    warnings: list[str] = []

    # 1. Title-based rejects (brand ambassador, entry-level, etc.)
    bad = _match_reject_title(cfg.reject_title_patterns, job.title)
    if bad:
        reasons.append(f"title matches reject pattern: {bad!r}")

    # 2. Citizenship / PR hard filter
    if extracted.dealbreakers.citizens_pr_only:
        evidence = extracted.dealbreakers.citizens_pr_evidence or "(no evidence captured)"
        reasons.append(f"citizens/PR only: {evidence}")

    # 3. Non-English language hard requirement
    blocked, langs = _language_blocked(extracted, cfg.reject_non_english_languages)
    if blocked:
        evidence = extracted.dealbreakers.language_evidence or "(no evidence captured)"
        reasons.append(f"non-English language required ({', '.join(langs)}): {evidence}")

    # 4. Seniority mismatch
    mismatch = _seniority_mismatch(
        extracted, cfg.target_seniority_min_years, cfg.target_seniority_max_years
    )
    if mismatch:
        reasons.append(
            f"seniority mismatch ({mismatch}): wants {extracted.years_experience_min}-{extracted.years_experience_max} yrs"
        )

    # Warnings (don't reject, but surface in digest)
    if extracted.dealbreakers.ambiguous_citizenship:
        warnings.append("citizenship ambiguous — employer may prefer local candidates")
    if (
        extracted.dealbreakers.sponsorship_available is False
        and not extracted.dealbreakers.citizens_pr_only
    ):
        warnings.append("JD states no sponsorship available")
    if (
        extracted.dealbreakers.requires_non_english_language
        and not blocked
    ):
        warnings.append(
            f"non-English languages mentioned: {', '.join(extracted.dealbreakers.non_english_languages)}"
        )

    if reasons:
        verdict = "reject"
    elif warnings:
        verdict = "borderline" if cfg.allow_borderline_citizenship else "reject"
    else:
        verdict = "keep"

    return GateResult(verdict=verdict, reasons=reasons, warnings=warnings)
