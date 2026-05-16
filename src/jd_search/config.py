"""Config loader.

Reads config/config.yaml + config/companies.yaml into typed Pydantic objects,
with env-var overrides for paths and secrets (OPENAI_API_KEY, GMAIL_*).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Secrets from .env / environment
# ---------------------------------------------------------------------------


class Secrets(BaseSettings):
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gmail_user: str = Field(default="", alias="GMAIL_USER")
    gmail_app_password: str = Field(default="", alias="GMAIL_APP_PASSWORD")
    email_to: str = Field(default="", alias="EMAIL_TO")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# ---------------------------------------------------------------------------
# config.yaml schema
# ---------------------------------------------------------------------------


class RunConfig(BaseModel):
    timezone: str = "Asia/Singapore"
    schedule_time: str = "07:00"


class ScoringConfig(BaseModel):
    model: str = "gpt-5-mini"
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    top_match_threshold: int = 75
    worth_a_look_threshold: int = 60
    max_jobs_per_run: int = 200


class GateConfig(BaseModel):
    allow_borderline_citizenship: bool = True
    reject_non_english_languages: list[str] = Field(default_factory=list)
    target_seniority_min_years: int = 4
    target_seniority_max_years: int = 10
    reject_title_patterns: list[str] = Field(default_factory=list)


class MCFSourceConfig(BaseModel):
    enabled: bool = True
    keywords: list[str] = Field(default_factory=list)
    position_levels: list[str] = Field(default_factory=list)
    min_years_experience: int = 4
    max_pages: int = 5


class WorkdaySourceConfig(BaseModel):
    enabled: bool = True
    pagination_limit: int = 100
    max_pages: int = 5
    request_delay_seconds: float = 1.0


class SmartRecruitersSourceConfig(BaseModel):
    enabled: bool = True
    country: str = "sg"
    pagination_limit: int = 100


class OracleHCMSourceConfig(BaseModel):
    enabled: bool = True
    pagination_limit: int = 25
    keyword: str = "Singapore"


class LinkedInSourceConfig(BaseModel):
    enabled: bool = False
    search_terms: list[str] = Field(default_factory=list)
    results_wanted: int = 30


class SourcesConfig(BaseModel):
    mycareersfuture: MCFSourceConfig = MCFSourceConfig()
    workday: WorkdaySourceConfig = WorkdaySourceConfig()
    smartrecruiters: SmartRecruitersSourceConfig = SmartRecruitersSourceConfig()
    oracle_hcm: OracleHCMSourceConfig = OracleHCMSourceConfig()
    linkedin_jobspy: LinkedInSourceConfig = LinkedInSourceConfig()


class EmailConfig(BaseModel):
    enabled: bool = True
    provider: str = "gmail_smtp"
    subject_template: str = "JD-Search digest {date}"
    send_empty: bool = True


class NotificationConfig(BaseModel):
    email: EmailConfig = EmailConfig()


class FiltersConfig(BaseModel):
    """Deterministic filters that run BEFORE the LLM.

    `relevant_title_keywords` is a case-insensitive substring match applied
    to job titles from adapters that aren't already keyword-filtered
    server-side (LVMH / Honeywell / Workday). MCF skips this because it
    filters by keyword at the API level.
    """

    relevant_title_keywords: list[str] = Field(
        default_factory=lambda: [
            "brand",
            "marketing",
            "campaign",
            "communications",
            "category",
            "go-to-market",
            "go to market",
            "gtm",
            "growth",
            "advertising",
        ]
    )


def title_is_relevant(title: str, keywords: list[str]) -> bool:
    """Return True if any keyword appears as a substring in the title (case-insensitive).

    An empty keyword list means "accept everything" — useful for adapters
    that are already keyword-filtered server-side.
    """
    if not keywords:
        return True
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)


class AppConfig(BaseModel):
    run: RunConfig = RunConfig()
    scoring: ScoringConfig = ScoringConfig()
    gate: GateConfig = GateConfig()
    filters: FiltersConfig = FiltersConfig()
    sources: SourcesConfig = SourcesConfig()
    notification: NotificationConfig = NotificationConfig()


# ---------------------------------------------------------------------------
# companies.yaml schema
# ---------------------------------------------------------------------------


class CompanyEntry(BaseModel):
    name: str
    ats: Literal["workday", "smartrecruiters", "oracle_hcm", "greenhouse", "lever", "ashby"]
    tenant: str | None = None

    # workday
    wd_shard: str | None = None
    wd_site: str | None = None

    # oracle_hcm
    oracle_pod: str | None = None
    oracle_site: str | None = None

    careers_url: str | None = None
    verified: bool = False
    verified_on: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def project_root() -> Path:
    """Repo root — the directory containing pyproject.toml."""
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> Any:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_app_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or Path(os.environ.get("JD_SEARCH_CONFIG", project_root() / "config/config.yaml"))
    return AppConfig.model_validate(_read_yaml(path))


def load_companies(companies_path: Path | None = None) -> list[CompanyEntry]:
    path = companies_path or Path(os.environ.get("JD_SEARCH_COMPANIES", project_root() / "config/companies.yaml"))
    data = _read_yaml(path)
    return [CompanyEntry.model_validate(c) for c in data.get("companies", [])]


def db_path() -> Path:
    return Path(os.environ.get("JD_SEARCH_DB", project_root() / "data/jobs.db"))


def digests_dir() -> Path:
    return Path(os.environ.get("JD_SEARCH_DIGESTS", project_root() / "digests"))


def resume_profile_path() -> Path:
    return project_root() / "data/resume_profile.txt"


def resume_dir() -> Path:
    """Where the candidate's LaTeX resume sources live.

    Set `JD_SEARCH_RESUME_DIR` to point at your own awesomecv folder.
    Defaults to `<repo>/resume/awesomecv` so the public repo doesn't bake
    in a personal directory name.
    """
    return Path(os.environ.get("JD_SEARCH_RESUME_DIR", project_root() / "resume/awesomecv"))


def candidate_name() -> str:
    """Read the candidate's display name from target_profile/profile.yaml.

    Used as the header in the parsed resume. Falls back to a placeholder
    if the profile is missing or doesn't define `candidate_name`.
    """
    path = project_root() / "target_profile/profile.yaml"
    if not path.exists():
        return "Candidate"
    try:
        data = _read_yaml(path) or {}
        return str(data.get("candidate_name") or "Candidate").strip()
    except Exception:
        return "Candidate"
