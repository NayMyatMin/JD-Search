"""Markdown digest writer + PDF renderer.

Produces `digests/YYYY-Www/YYYY-MM-DD.md` (ISO week). The same markdown is
the body of the email, rendered to HTML by markdown-it-py, and optionally
rendered to PDF via weasyprint.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from .config import ScoringConfig, digests_dir
from .models import ApplyLinks, ExtractedJD, GateResult, JobScore

# macOS Homebrew ships Cairo/Pango in /opt/homebrew/lib; Python's dynamic
# loader doesn't pick them up automatically. Set the env var at import time
# so `from weasyprint import HTML` downstream just works.
if sys.platform == "darwin":
    _brew_lib = "/opt/homebrew/lib"
    if Path(_brew_lib).exists():
        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if _brew_lib not in existing:
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
                f"{_brew_lib}:{existing}" if existing else _brew_lib
            )


@dataclass
class DigestCounts:
    fetched: int
    new: int
    kept: int
    borderline: int
    rejected: int
    top_count: int
    borderline_count: int


def _row_to_parts(
    row: dict[str, Any],
) -> tuple[JobScore | None, ExtractedJD | None, GateResult | None, ApplyLinks | None]:
    score = JobScore.model_validate_json(row["score_json"]) if row.get("score_json") else None
    extracted = (
        ExtractedJD.model_validate_json(row["extracted_json"])
        if row.get("extracted_json")
        else None
    )
    gate = GateResult.model_validate_json(row["gate_json"]) if row.get("gate_json") else None
    apply_links = (
        ApplyLinks.model_validate_json(row["apply_links_json"])
        if row.get("apply_links_json")
        else None
    )
    return score, extracted, gate, apply_links


def render_digest(
    run_date: date,
    rows: list[dict[str, Any]],
    counts: DigestCounts,
    cfg: ScoringConfig,
) -> str:
    """Build the markdown digest body."""
    top_thresh = cfg.top_match_threshold
    mid_thresh = cfg.worth_a_look_threshold

    top: list[dict[str, Any]] = []
    borderline: list[dict[str, Any]] = []
    for r in rows:
        score = r.get("final_score") or -1
        verdict = r.get("verdict") or ""
        if verdict == "reject":
            continue
        if score >= top_thresh and verdict == "keep":
            top.append(r)
        elif score >= mid_thresh:
            borderline.append(r)

    top.sort(key=lambda r: r.get("final_score", 0), reverse=True)
    borderline.sort(key=lambda r: r.get("final_score", 0), reverse=True)

    iso_year, iso_week, _ = run_date.isocalendar()
    out: list[str] = []
    out.append(f"# JD-Search Daily Digest — {run_date.isoformat()}")
    out.append("")
    out.append(
        f"Week {iso_year}-W{iso_week:02d} · fetched {counts.fetched} · new {counts.new} · "
        f"kept {counts.kept} · borderline {counts.borderline} · rejected {counts.rejected}"
    )
    out.append("")

    out.append(f"## Top matches (score ≥ {top_thresh})")
    if not top:
        out.append("*No new top matches today.*")
    else:
        for r in top:
            out.extend(_render_row(r, is_top=True))
    out.append("")

    out.append(f"## Worth a look ({mid_thresh}–{top_thresh - 1})")
    if not borderline:
        out.append("*Nothing in the borderline band today.*")
    else:
        for r in borderline:
            out.extend(_render_row(r, is_top=False))
    out.append("")

    out.append("---")
    out.append(f"Generated {run_date.isoformat()} · JD-Search v0.1")
    return "\n".join(out) + "\n"


def _render_row(r: dict[str, Any], is_top: bool) -> list[str]:
    score, extracted, gate, apply_links = _row_to_parts(r)
    lines: list[str] = []
    title = r.get("title") or "(untitled)"
    company = r.get("company") or "Unknown"
    location = r.get("location") or ""
    url = r.get("url") or ""
    s = score.score if score else r.get("final_score", -1)

    lines.append(f"### [{s}] {title} — {company}")
    lines.append(f"*{location}* · [{url}]({url})")
    apply_line = _apply_link_line(apply_links)
    if apply_line:
        lines.append(apply_line)
    if score:
        lines.append(
            f"- **Anchor similarity:** {score.anchor_similarity} · "
            f"**Resume fit:** {score.resume_fit}"
        )
        if score.strengths:
            lines.append(f"- **Strengths:** {', '.join(score.strengths)}")
        if score.gaps:
            lines.append(f"- **Gaps:** {', '.join(score.gaps)}")
        lines.append(f"- {score.explanation}")
    if gate and gate.warnings:
        for w in gate.warnings:
            lines.append(f"- ⚠️ {w}")
    if extracted:
        lines.append(
            f"- *Seniority:* {extracted.seniority} "
            f"({extracted.years_experience_min or '?'}–{extracted.years_experience_max or '?'} yrs) · "
            f"*Role family:* {extracted.role_family}"
        )
    lines.append("")
    return lines


def _apply_link_line(links: ApplyLinks | None) -> str:
    """Render the optional `- **Apply at:** ...` bullet for a row.

    Returns empty string if no validated links exist.
    """
    if not links:
        return ""
    parts: list[str] = []
    if links.linkedin:
        parts.append(f"[LinkedIn]({links.linkedin.url})")
    if links.company:
        parts.append(f"[Company careers]({links.company.url})")
    if links.other:
        parts.append(f"[Apply page]({links.other.url})")
    if not parts:
        return ""
    return "- **Apply at:** " + " · ".join(parts)


def write_digest(run_date: date, body: str) -> Path:
    iso_year, iso_week, _ = run_date.isocalendar()
    week_dir = digests_dir() / f"{iso_year}-W{iso_week:02d}"
    week_dir.mkdir(parents=True, exist_ok=True)
    path = week_dir / f"{run_date.isoformat()}.md"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# Regenerate digest from stored DB rows (used by `jd digest`)
# ---------------------------------------------------------------------------


def regenerate_from_store(run_date: date, store, cfg: ScoringConfig) -> Path:
    """Rebuild the .md for `run_date` from the current DB state.

    Does not re-run the pipeline — just re-renders what's already scored.
    Useful after config changes (thresholds, reject patterns) or after
    manually flipping the `dismissed` flag.
    """
    rows = store.iter_scored_today(run_date.isoformat())
    counts = DigestCounts(
        fetched=len(rows),
        new=0,
        kept=sum(1 for r in rows if r.get("verdict") == "keep"),
        borderline=sum(1 for r in rows if r.get("verdict") == "borderline"),
        rejected=sum(1 for r in rows if r.get("verdict") == "reject"),
        top_count=sum(1 for r in rows if (r.get("final_score") or 0) >= cfg.top_match_threshold),
        borderline_count=sum(
            1
            for r in rows
            if cfg.worth_a_look_threshold
            <= (r.get("final_score") or 0)
            < cfg.top_match_threshold
        ),
    )
    body = render_digest(run_date, rows, counts, cfg)
    return write_digest(run_date, body)


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------


_PDF_CSS = """
@page {
    size: A4;
    margin: 18mm 16mm 18mm 16mm;
    @bottom-right {
        content: "JD-Search · page " counter(page) " of " counter(pages);
        font-size: 9pt;
        color: #888;
    }
}
body {
    font-family: "Helvetica Neue", "Helvetica", "Arial", sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #222;
}
h1 {
    font-size: 20pt;
    margin: 0 0 6pt 0;
    padding-bottom: 4pt;
    border-bottom: 1.5pt solid #333;
}
h2 {
    font-size: 14pt;
    margin-top: 18pt;
    margin-bottom: 8pt;
    color: #444;
    page-break-after: avoid;
}
h3 {
    font-size: 11.5pt;
    margin-top: 12pt;
    margin-bottom: 4pt;
    color: #0b3d8a;
    page-break-after: avoid;
}
p, ul, li {
    margin: 3pt 0;
}
ul {
    padding-left: 16pt;
}
li {
    margin-bottom: 2pt;
}
a {
    color: #0b5fff;
    text-decoration: none;
    word-break: break-all;
}
em, i {
    color: #555;
}
strong {
    color: #111;
}
hr {
    border: none;
    border-top: 0.5pt solid #ccc;
    margin: 14pt 0 8pt 0;
}
.run-meta {
    color: #666;
    font-size: 9.5pt;
    margin-bottom: 10pt;
}
"""


def render_pdf(markdown_body: str, out_path: Path) -> Path:
    """Render a digest markdown file to a styled PDF next to it."""
    from weasyprint import CSS, HTML  # imported lazily — heavy

    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
    html_body = md.render(markdown_body)
    full_html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>JD-Search Digest</title></head>
<body>{html_body}</body></html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=full_html).write_pdf(str(out_path), stylesheets=[CSS(string=_PDF_CSS)])
    return out_path


def md_to_pdf_path(md_path: Path) -> Path:
    return md_path.with_suffix(".pdf")
