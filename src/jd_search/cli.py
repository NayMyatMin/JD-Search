"""Typer CLI entrypoint."""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from . import __version__, pipeline
from .adapters.mycareersfuture import MyCareersFutureScraper
from .config import (
    Secrets,
    db_path,
    digests_dir,
    load_app_config,
    load_companies,
)
from .digest import md_to_pdf_path, regenerate_from_store, render_pdf
from .notify import send_email
from .resume import build_profile
from .store import Store

app = typer.Typer(add_completion=False, help="JD-Search — personal SG job agent")
console = Console()


@app.callback()
def _root(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"jd-search {__version__}")


@app.command("build-profile")
def build_profile_cmd() -> None:
    """Parse the LaTeX resume into data/resume_profile.txt."""
    text = build_profile()
    console.print(f"[green]wrote {len(text)} chars[/green]")
    console.print(text[:1000] + ("…" if len(text) > 1000 else ""))


@app.command()
def run(
    no_email: bool = typer.Option(False, "--no-email", help="Do not send email after run"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip LLM calls (fetch only)"),
    limit: int = typer.Option(0, "--limit", help="Cap LLM-scored postings (0 = config default)"),
) -> None:
    """Run the full pipeline: fetch → extract → gate → score → digest → email."""
    path = pipeline.run(send=not no_email, dry_run=dry_run, limit=limit or None)
    console.print(f"[green]digest written[/green] → {path}")


@app.command("fetch-only")
def fetch_only(source: str = typer.Option("mycareersfuture", help="Source to test")) -> None:
    """Smoke-test a single adapter without any LLM calls."""
    cfg = load_app_config()
    companies = load_companies()
    if source == "mycareersfuture":
        s = MyCareersFutureScraper(cfg.sources.mycareersfuture)
    else:
        console.print(f"[red]unknown source:[/red] {source}")
        raise typer.Exit(1)
    jobs = s.fetch()

    table = Table(title=f"{source}: {len(jobs)} postings")
    table.add_column("Title", overflow="fold")
    table.add_column("Company", overflow="fold")
    table.add_column("Years")
    table.add_column("Level")
    for j in jobs[:25]:
        raw = j.raw or {}
        levels = ", ".join(raw.get("positionLevels") or [])
        table.add_row(j.title[:60], j.company[:30], str(raw.get("minimumYearsExperience") or ""), levels)
    console.print(table)
    console.print(f"[dim]showing first 25 of {len(jobs)}[/dim]")


def _iso_week_dir(the_date: date) -> Path:
    iso_year, iso_week, _ = the_date.isocalendar()
    return digests_dir() / f"{iso_year}-W{iso_week:02d}"


def _resolve_digest_md(date_str: str | None) -> Path:
    """Return the .md path for the given date (default: today)."""
    the_date = date.fromisoformat(date_str) if date_str else date.today()
    return _iso_week_dir(the_date) / f"{the_date.isoformat()}.md"


@app.command()
def digest(
    date_str: str = typer.Option("", "--date", help="YYYY-MM-DD (default: today)"),
    pdf: bool = typer.Option(False, "--pdf", help="Also render a PDF next to the .md"),
) -> None:
    """Regenerate the markdown digest for a date from the current DB.

    Does NOT re-run the pipeline — useful after tuning thresholds or adding
    new reject patterns, when you want the digest file to reflect the
    updated rules without paying for more LLM calls.
    """
    cfg = load_app_config()
    store = Store(db_path())
    the_date = date.fromisoformat(date_str) if date_str else date.today()
    md_path = regenerate_from_store(the_date, store, cfg.scoring)
    console.print(f"[green]digest regenerated[/green] → {md_path}")
    if pdf:
        pdf_path = render_pdf(md_path.read_text(), md_to_pdf_path(md_path))
        console.print(f"[green]pdf written[/green] → {pdf_path}")


@app.command("pdf")
def pdf_cmd(
    date_str: str = typer.Option("", "--date", help="YYYY-MM-DD (default: today)"),
    output: str = typer.Option("", "--output", "-o", help="Override output path"),
) -> None:
    """Render an existing digest markdown file to PDF (in the same folder by default)."""
    md_path = _resolve_digest_md(date_str or None)
    if not md_path.exists():
        console.print(f"[red]no digest at[/red] {md_path}")
        console.print("[dim]run `jd digest` first, or `jd run`[/dim]")
        raise typer.Exit(1)
    out = Path(output) if output else md_to_pdf_path(md_path)
    render_pdf(md_path.read_text(), out)
    size = out.stat().st_size
    console.print(f"[green]pdf written[/green] → {out}  ({size:,} bytes)")


@app.command("email-test")
def email_test() -> None:
    """Send a tiny test email via Gmail SMTP to confirm credentials work."""
    cfg = load_app_config()
    secrets = Secrets()
    ok = send_email(
        subject=f"JD-Search test {date.today().isoformat()}",
        markdown_body="# JD-Search test\n\nIf you can read this, Gmail SMTP is wired up correctly.",
        cfg=cfg.notification.email,
        secrets=secrets,
    )
    if ok:
        console.print("[green]email sent[/green]")
    else:
        console.print("[red]email failed — check .env and app password[/red]")
        raise typer.Exit(1)


@app.command("verify-workday")
def verify_workday(url: str = typer.Argument(..., help="Careers page URL")) -> None:
    """Probe a Workday careers URL and print a companies.yaml block you can paste.

    Example:
        jd verify-workday https://unilever.wd3.myworkdayjobs.com/Unilever_Experienced_Professionals
    """
    import re

    m = re.match(
        r"https?://(?P<tenant>[^.]+)\.(?P<shard>wd\d+)\.myworkdayjobs\.com/(?:en-US/)?(?P<site>[^/?#]+)",
        url,
    )
    if not m:
        console.print("[red]URL does not match Workday pattern[/red]")
        raise typer.Exit(1)
    tenant, shard, site = m.group("tenant"), m.group("shard"), m.group("site")
    cxs = f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    console.print(f"probing [cyan]{cxs}[/cyan]")

    try:
        r = httpx.post(
            cxs,
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    except Exception as exc:
        console.print(f"[red]request failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception:
        console.print(r.text[:400])
        raise typer.Exit(1)

    total = data.get("total")
    postings = data.get("jobPostings") or []
    sg = [p for p in postings if "Singapore" in (p.get("locationsText") or "")]

    table = Table(title="sample")
    table.add_column("Title", overflow="fold")
    table.add_column("Location")
    for p in postings[:5]:
        table.add_row((p.get("title") or "")[:60], (p.get("locationsText") or "")[:40])
    console.print(table)

    console.print(f"[bold]total postings:[/bold] {total}")
    console.print(f"[bold]Singapore in first page:[/bold] {len(sg)}")

    if total and total > 0:
        console.print("\n[green]Looks good. Paste this into config/companies.yaml:[/green]\n")
        console.print(
            f"""  - name: {tenant.upper()}
    ats: workday
    tenant: {tenant}
    wd_shard: {shard}
    wd_site: {site}
    careers_url: {url}
    verified: true
    verified_on: "{date.today().isoformat()}"
"""
        )
    else:
        console.print("[yellow]endpoint returned no postings — site path may be wrong[/yellow]")


if __name__ == "__main__":
    app()
