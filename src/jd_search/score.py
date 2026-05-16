"""GPT-5-mini rubric scoring.

Called ONLY for jobs that passed the gate. Produces a JobScore with
score / anchor_similarity / resume_fit / strengths / gaps / explanation.

The "target anchor" is the combination of the four reference JDs plus
profile.yaml + the plaintext resume. These are loaded once and reused
for every call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import ScoringConfig, project_root
from .models import ExtractedJD, JobPost, JobScore
from .resume import load_profile

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a brutal, practical job-fit scorer. Given the
candidate profile, four REFERENCE JDs representing the candidate's ideal
role shape, and a CANDIDATE POSTING, score the fit from 0 to 100.

Scoring rubric:
- 90-100: exceptional match to reference JDs and resume
- 75-89:  strong match, apply immediately
- 60-74:  partial match, worth a look
- 40-59:  tangentially related, skim only
- <40:    wrong shape

Report three sub-scores:
- anchor_similarity: how closely the posting matches the REFERENCE JDs
  (role family, scope, seniority, sector)
- resume_fit: how well the candidate's experience maps to the posting's
  requirements
- score: final blended score — weigh anchor_similarity 60% and resume_fit 40%

Then list up to 3 strengths and 3 gaps from the candidate's perspective.
Explanation must be ≤40 words, practical, and mention at least one
specific reason.

Be honest: generic buzzwords ("fast-paced environment", "team player")
should NOT inflate the score."""


def _load_reference_jds() -> str:
    ref_dir = project_root() / "target_profile" / "reference_jds"
    parts: list[str] = []
    if ref_dir.exists():
        for p in sorted(ref_dir.glob("*.md")):
            parts.append(f"--- {p.stem} ---\n{p.read_text()}")
    return "\n\n".join(parts)


def _load_profile_yaml() -> str:
    p = project_root() / "target_profile" / "profile.yaml"
    return p.read_text() if p.exists() else ""


class Scorer:
    def __init__(self, cfg: ScoringConfig, api_key: str) -> None:
        self.cfg = cfg
        self.client = OpenAI(api_key=api_key)
        self._resume = load_profile()
        self._reference_jds = _load_reference_jds()
        self._profile_yaml = _load_profile_yaml()

    def _user_prompt(self, job: JobPost, extracted: ExtractedJD) -> str:
        return f"""CANDIDATE PROFILE
===
{self._profile_yaml}

CANDIDATE RESUME
===
{self._resume[:4000]}

REFERENCE JDs (the candidate's ideal role shape)
===
{self._reference_jds[:8000]}

CANDIDATE POSTING TO SCORE
===
Title: {job.title}
Company: {job.company}
Location: {job.location}
Seniority (extracted): {extracted.seniority} / {extracted.years_experience_min}-{extracted.years_experience_max} yrs
Role family: {extracted.role_family}
Sectors: {", ".join(extracted.sectors)}
Summary: {extracted.summary}
Must-have skills: {", ".join(extracted.must_have_skills)}

Full description:
{job.description[:6000]}
"""

    def score(self, job: JobPost, extracted: ExtractedJD) -> JobScore | None:
        try:
            resp = self.client.responses.parse(
                model=self.cfg.model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": self._user_prompt(job, extracted)},
                ],
                text_format=JobScore,
                reasoning={"effort": self.cfg.reasoning_effort},
            )
        except Exception as exc:
            log.warning("score failed for %s / %s: %s", job.company, job.title, exc)
            return None

        parsed: Any = getattr(resp, "output_parsed", None)
        if isinstance(parsed, JobScore):
            return parsed
        try:
            return JobScore.model_validate(parsed)
        except Exception as exc:
            log.warning("score parse failed for %s: %s", job.title, exc)
            return None
