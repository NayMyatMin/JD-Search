"""Gate logic tests — no LLM calls."""

from __future__ import annotations

from jd_search.config import GateConfig
from jd_search.gate import run_gate
from jd_search.models import Dealbreakers, ExtractedJD, JobPost


def _job(title: str = "Brand Manager") -> JobPost:
    return JobPost(
        source="test",
        title=title,
        company="Test Co",
        location="Singapore",
        url="https://example.com/1",
        description="",
    )


def _extracted(**kwargs) -> ExtractedJD:
    defaults = dict(
        title_clean="Brand Manager",
        seniority="manager",
        years_experience_min=5,
        years_experience_max=8,
        role_family="Brand Marketing",
        sectors=["FMCG"],
        geographic_scope=["SEA"],
        must_have_skills=[],
        nice_to_have_skills=[],
        dealbreakers=Dealbreakers(
            citizens_pr_only=False,
            requires_non_english_language=False,
            seniority_fit="target",
        ),
        summary="Brand manager role.",
    )
    defaults.update(kwargs)
    return ExtractedJD(**defaults)


_DEFAULT_CFG = GateConfig(
    allow_borderline_citizenship=True,
    reject_non_english_languages=["Mandarin", "Malay", "Tamil"],
    target_seniority_min_years=4,
    target_seniority_max_years=10,
    reject_title_patterns=["brand ambassador", "entry[- ]level"],
)


def test_clean_keep() -> None:
    result = run_gate(_job(), _extracted(), _DEFAULT_CFG)
    assert result.verdict == "keep"
    assert not result.reasons


def test_citizens_only_rejects() -> None:
    e = _extracted(
        dealbreakers=Dealbreakers(
            citizens_pr_only=True,
            citizens_pr_evidence="Only Singaporeans and PRs need apply.",
            requires_non_english_language=False,
            seniority_fit="target",
        )
    )
    result = run_gate(_job(), e, _DEFAULT_CFG)
    assert result.verdict == "reject"
    assert any("citizens/PR only" in r for r in result.reasons)


def test_mandarin_required_rejects() -> None:
    e = _extracted(
        dealbreakers=Dealbreakers(
            citizens_pr_only=False,
            requires_non_english_language=True,
            non_english_languages=["Mandarin"],
            language_evidence="Must be fluent in Mandarin.",
            seniority_fit="target",
        )
    )
    result = run_gate(_job(), e, _DEFAULT_CFG)
    assert result.verdict == "reject"
    assert any("non-English" in r for r in result.reasons)


def test_brand_ambassador_title_rejects() -> None:
    result = run_gate(_job("Brand Ambassador (Entry Level)"), _extracted(), _DEFAULT_CFG)
    assert result.verdict == "reject"


def test_ambiguous_citizenship_borderline() -> None:
    e = _extracted(
        dealbreakers=Dealbreakers(
            citizens_pr_only=False,
            ambiguous_citizenship=True,
            requires_non_english_language=False,
            seniority_fit="target",
        )
    )
    result = run_gate(_job(), e, _DEFAULT_CFG)
    assert result.verdict == "borderline"
    assert result.warnings


def test_too_junior_rejects() -> None:
    e = _extracted(years_experience_min=0, years_experience_max=2)
    result = run_gate(_job(), e, _DEFAULT_CFG)
    assert result.verdict == "reject"
    assert any("seniority" in r for r in result.reasons)
