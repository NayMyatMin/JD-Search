"""Unit tests for apply_links normalization + fuzzy matching."""

from __future__ import annotations

from jd_search.apply_links import fuzzy_title_match, normalize_title


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------


def test_normalize_strips_leading_job_id() -> None:
    assert normalize_title("26944441 Regional Marketing Senior Analyst") == (
        "regional marketing senior analyst"
    )


def test_normalize_strips_parentheticals() -> None:
    assert normalize_title("Marketing Manager (Brand & Growth)") == "marketing manager"


def test_normalize_strips_contract_duration() -> None:
    assert normalize_title(
        "Manager, Marketing & Communications (6-9 Months Contract | OOH)"
    ) == "manager, marketing & communications"


def test_normalize_strips_new_tag_and_seniority_fork() -> None:
    assert normalize_title("-NEW- 7 Months Assistant Marketing Manager") == (
        "assistant marketing manager"
    )
    assert normalize_title("Senior / Marketing Communications Manager") == (
        "marketing communications manager"
    )


def test_normalize_collapses_whitespace() -> None:
    assert normalize_title("  Marketing   Manager  ") == "marketing manager"


# ---------------------------------------------------------------------------
# fuzzy_title_match — confidence thresholds
# ---------------------------------------------------------------------------


def test_fuzzy_identical_titles_max_confidence() -> None:
    assert fuzzy_title_match("Marketing Manager", "Marketing Manager") == 100


def test_fuzzy_parenthetical_suffix_high_confidence() -> None:
    # Real-world: MCF posts "Marketing Manager" and LinkedIn posts
    # "Marketing Manager (Brand & Growth)". These should match strongly.
    score = fuzzy_title_match(
        "Marketing Manager", "Marketing Manager (Brand & Growth)"
    )
    assert score >= 85, f"expected ≥85, got {score}"


def test_fuzzy_word_order_high_confidence() -> None:
    # token_sort_ratio handles reordering — "Brand Marketing Manager" vs
    # "Marketing Manager - Brand" should still score well.
    score = fuzzy_title_match(
        "Brand Marketing Manager", "Marketing Manager - Brand"
    )
    assert score >= 85, f"expected ≥85, got {score}"


def test_fuzzy_unrelated_titles_low_confidence() -> None:
    assert fuzzy_title_match("Marketing Manager", "Senior Software Engineer") < 60


def test_fuzzy_empty_inputs_return_zero() -> None:
    assert fuzzy_title_match("", "Marketing Manager") == 0
    assert fuzzy_title_match("Marketing Manager", "") == 0


def test_fuzzy_jd_with_id_matches_clean_company_page_title() -> None:
    # MCF prepends numeric IDs to some Citi roles. The company career page
    # would show "Regional Marketing Senior Analyst, AVP". These should match.
    score = fuzzy_title_match(
        "26944441 Regional Marketing Senior Analyst - Assistant Vice President",
        "Regional Marketing Senior Analyst - Assistant Vice President | Citi",
    )
    assert score >= 85, f"expected ≥85, got {score}"


def test_fuzzy_handles_page_with_repeated_title() -> None:
    # Career pages typically have the role title in BOTH <title> and <h1>,
    # roughly doubling the token count vs the JD. token_sort_ratio penalizes
    # this; token_set_ratio (what we use) should not.
    jd = "Regional Marketing Senior Analyst - Assistant Vice President"
    combined = (
        "Regional Marketing Senior Analyst - Assistant Vice President | Citi Careers "
        "Regional Marketing Senior Analyst - Assistant Vice President"
    )
    score = fuzzy_title_match(jd, combined)
    assert score >= 85, f"expected ≥85, got {score}"
