"""Pipeline orchestrator.

Stages:
    1. Fetch raw postings from all enabled adapters
    2. Dedup + persist in sqlite
    3. For each pending posting: extract (LLM) → gate → score (LLM)
    4. Render markdown digest for today
    5. Send email (unless --no-email)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from .adapters.base import Scraper
from .adapters.mycareersfuture import MyCareersFutureScraper
from .adapters.oracle_hcm import OracleHCMScraper
from .adapters.smartrecruiters import SmartRecruitersScraper
from .adapters.workday import WorkdayScraper
from .config import (
    AppConfig,
    Secrets,
    db_path,
    load_app_config,
    load_companies,
)
from .digest import DigestCounts, render_digest, write_digest
from .extract import Extractor
from .gate import run_gate
from .models import ExtractedJD, JobPost
from .notify import send_email
from .score import Scorer
from .store import Store

log = logging.getLogger(__name__)


def build_scrapers(cfg: AppConfig, companies: list) -> list[Scraper]:
    title_kws = cfg.filters.relevant_title_keywords
    scrapers: list[Scraper] = []
    if cfg.sources.mycareersfuture.enabled:
        # MCF is already keyword-filtered at the API level; don't double-filter.
        scrapers.append(MyCareersFutureScraper(cfg.sources.mycareersfuture))
    if cfg.sources.workday.enabled:
        scrapers.append(
            WorkdayScraper(cfg.sources.workday, companies, title_keywords=title_kws)
        )
    if cfg.sources.smartrecruiters.enabled:
        scrapers.append(
            SmartRecruitersScraper(
                cfg.sources.smartrecruiters, companies, title_keywords=title_kws
            )
        )
    if cfg.sources.oracle_hcm.enabled:
        scrapers.append(
            OracleHCMScraper(cfg.sources.oracle_hcm, companies, title_keywords=title_kws)
        )
    return scrapers


def _round_robin(pending: list[dict]) -> list[dict]:
    """Interleave pending rows across sources so no single source monopolizes budget."""
    buckets: dict[str, list[dict]] = {}
    for row in pending:
        buckets.setdefault(row.get("source", ""), []).append(row)
    if not buckets:
        return []
    max_per = max(len(v) for v in buckets.values())
    out: list[dict] = []
    for i in range(max_per):
        for src in buckets:
            if i < len(buckets[src]):
                out.append(buckets[src][i])
    return out


def run(
    send: bool = True,
    dry_run: bool = False,
    run_date: date | None = None,
    limit: int | None = None,
) -> Path:
    """Run the full pipeline. Returns the path to the written digest file."""
    cfg = load_app_config()
    companies = load_companies()
    secrets = Secrets()
    store = Store(db_path())
    run_date = run_date or date.today()
    started = datetime.utcnow().isoformat()
    log.info("JD-Search run starting at %s", started)

    # --- 1. Fetch ---------------------------------------------------------
    scrapers = build_scrapers(cfg, companies)
    all_fetched: list[JobPost] = []
    for s in scrapers:
        try:
            batch = s.fetch()
            log.info("adapter %s → %d postings", s.name, len(batch))
            all_fetched.extend(batch)
        except Exception as exc:
            log.warning("adapter %s raised: %s", s.name, exc)

    # --- 2. Dedup + persist -----------------------------------------------
    new_count = 0
    for job in all_fetched:
        _, is_new = store.upsert_fetched(job)
        if is_new:
            new_count += 1
    log.info("%d fetched, %d new", len(all_fetched), new_count)

    # --- 3. Extract → gate → score ----------------------------------------
    kept = borderline = rejected = 0
    if dry_run:
        log.info("dry-run: skipping LLM calls")
    else:
        extractor = Extractor(cfg.scoring, secrets.openai_api_key) if secrets.openai_api_key else None
        scorer = Scorer(cfg.scoring, secrets.openai_api_key) if secrets.openai_api_key else None
        if not extractor or not scorer:
            log.error("OPENAI_API_KEY missing — cannot run extract/score")
        else:
            pending = store.iter_pending(
                scoring_version=1,
                title_keywords=cfg.filters.relevant_title_keywords,
            )
            pending = _round_robin(pending)
            cap = limit if limit is not None else cfg.scoring.max_jobs_per_run
            pending = pending[:cap]
            # Log breakdown by source so we can see the round-robin working
            by_src: dict[str, int] = {}
            for row in pending:
                by_src[row.get("source", "")] = by_src.get(row.get("source", ""), 0) + 1
            log.info(
                "scoring %d pending postings with %s (by source: %s)",
                len(pending),
                cfg.scoring.model,
                by_src,
            )

            for row in pending:
                job = _row_to_jobpost(row)
                extracted: ExtractedJD | None = extractor.extract(job)
                if not extracted:
                    continue
                store.set_extracted(row["id"], extracted, scoring_version=1)

                gate_result = run_gate(job, extracted, cfg.gate)
                store.set_gate(row["id"], gate_result)

                if gate_result.verdict == "reject":
                    rejected += 1
                    continue

                score = scorer.score(job, extracted)
                if score:
                    store.set_score(row["id"], score)

                if gate_result.verdict == "keep":
                    kept += 1
                elif gate_result.verdict == "borderline":
                    borderline += 1

    # --- 4. Digest --------------------------------------------------------
    rows = store.iter_scored_today(run_date.isoformat())
    counts = DigestCounts(
        fetched=len(all_fetched),
        new=new_count,
        kept=kept,
        borderline=borderline,
        rejected=rejected,
        top_count=sum(
            1 for r in rows if (r.get("final_score") or 0) >= cfg.scoring.top_match_threshold
        ),
        borderline_count=sum(
            1
            for r in rows
            if cfg.scoring.worth_a_look_threshold
            <= (r.get("final_score") or 0)
            < cfg.scoring.top_match_threshold
        ),
    )
    body = render_digest(run_date, rows, counts, cfg.scoring)
    path = write_digest(run_date, body)
    log.info("digest written → %s", path)

    for r in rows:
        store.mark_in_digest(r["id"], run_date.isoformat())

    store.record_run(
        started, len(all_fetched), new_count, kept, borderline, rejected, notes=""
    )

    # --- 5. Email ---------------------------------------------------------
    if send and cfg.notification.email.enabled:
        if counts.top_count or counts.borderline_count or cfg.notification.email.send_empty:
            subject = cfg.notification.email.subject_template.format(
                date=run_date.isoformat(),
                top_count=counts.top_count,
                borderline_count=counts.borderline_count,
            )
            send_email(subject, body, cfg.notification.email, secrets)

    return path


def _row_to_jobpost(row: dict) -> JobPost:
    return JobPost(
        source=row.get("source", ""),
        source_id=row.get("source_id") or None,
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        url=row.get("url", ""),
        description=row.get("description", ""),
        posted_on=row.get("posted_on") or None,
    )
