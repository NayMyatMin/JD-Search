"""SQLite persistence via sqlite-utils.

Tables:
    jobs         one row per unique posting (keyed by content_hash)
    runs         one row per pipeline run
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import sqlite_utils

from .models import ApplyLinks, ExtractedJD, GateResult, JobPost, JobScore, StoredJob


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite_utils.Database(str(path))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if "jobs" not in self.db.table_names():
            self.db["jobs"].create(
                {
                    "id": str,
                    "source": str,
                    "source_id": str,
                    "title": str,
                    "company": str,
                    "location": str,
                    "url": str,
                    "description": str,
                    "posted_on": str,
                    "first_seen_at": str,
                    "last_seen_at": str,
                    "extracted_json": str,
                    "gate_json": str,
                    "score_json": str,
                    "apply_links_json": str,
                    "final_score": int,
                    "verdict": str,
                    "seen_in_digest_on": str,
                    "dismissed": int,
                    "scoring_version": int,
                },
                pk="id",
                not_null={"id", "source", "title", "company", "url"},
            )
            self.db["jobs"].create_index(["verdict"])
            self.db["jobs"].create_index(["final_score"])
            self.db["jobs"].create_index(["seen_in_digest_on"])
        else:
            # Migration: add apply_links_json on existing DBs that pre-date this column.
            existing_cols = {c.name for c in self.db["jobs"].columns}
            if "apply_links_json" not in existing_cols:
                self.db["jobs"].add_column("apply_links_json", str, not_null_default="")

        if "runs" not in self.db.table_names():
            self.db["runs"].create(
                {
                    "started_at": str,
                    "finished_at": str,
                    "fetched": int,
                    "new": int,
                    "kept": int,
                    "borderline": int,
                    "rejected": int,
                    "notes": str,
                },
                pk="started_at",
            )

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------

    def upsert_fetched(self, job: JobPost) -> tuple[str, bool]:
        """Insert a freshly fetched JobPost (no extraction/score yet).

        Returns (id, is_new).
        """
        jid = job.content_hash()
        existing = self.db["jobs"].rows_where("id = ?", [jid])
        rows = list(existing)
        now = datetime.utcnow().isoformat()
        if rows:
            self.db["jobs"].update(jid, {"last_seen_at": now})
            return jid, False
        self.db["jobs"].insert(
            {
                "id": jid,
                "source": job.source,
                "source_id": job.source_id or "",
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.url,
                "description": job.description,
                "posted_on": job.posted_on or "",
                "first_seen_at": now,
                "last_seen_at": now,
                "extracted_json": "",
                "gate_json": "",
                "score_json": "",
                "apply_links_json": "",
                "final_score": -1,
                "verdict": "pending",
                "seen_in_digest_on": "",
                "dismissed": 0,
                "scoring_version": 1,
            },
            pk="id",
        )
        return jid, True

    def has_extracted(self, jid: str, scoring_version: int) -> bool:
        row = self.db["jobs"].get(jid)
        return (
            bool(row.get("extracted_json"))
            and int(row.get("scoring_version") or 0) == scoring_version
        )

    def set_extracted(self, jid: str, extracted: ExtractedJD, scoring_version: int) -> None:
        self.db["jobs"].update(
            jid,
            {
                "extracted_json": extracted.model_dump_json(),
                "scoring_version": scoring_version,
            },
        )

    def set_gate(self, jid: str, gate: GateResult) -> None:
        self.db["jobs"].update(
            jid, {"gate_json": gate.model_dump_json(), "verdict": gate.verdict}
        )

    def set_score(self, jid: str, score: JobScore) -> None:
        self.db["jobs"].update(
            jid, {"score_json": score.model_dump_json(), "final_score": score.score}
        )

    def set_apply_links(self, jid: str, links: ApplyLinks) -> None:
        self.db["jobs"].update(jid, {"apply_links_json": links.model_dump_json()})

    def has_apply_links(self, jid: str) -> bool:
        row = self.db["jobs"].get(jid)
        return bool(row.get("apply_links_json"))

    def iter_top_matches_needing_apply_links(
        self, the_date: str, min_score: int
    ) -> list[dict[str, Any]]:
        """Today's keepers above `min_score` that don't yet have apply links."""
        rows = self.db.query(
            """
            SELECT * FROM jobs
            WHERE final_score >= ?
              AND verdict = 'keep'
              AND first_seen_at LIKE ?
              AND (apply_links_json IS NULL OR apply_links_json = '')
            ORDER BY final_score DESC
            """,
            [min_score, f"{the_date}%"],
        )
        return list(rows)

    def mark_in_digest(self, jid: str, on_date: str) -> None:
        self.db["jobs"].update(jid, {"seen_in_digest_on": on_date})

    def iter_pending(
        self,
        scoring_version: int,
        title_keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Jobs that need extraction/scoring (or re-scoring if version bumped).

        If `title_keywords` is provided, only rows whose title contains one
        of the keywords (case-insensitive substring) are returned. This lets
        us skip already-persisted irrelevant rows without deleting them.
        """
        where = ["(extracted_json = '' OR scoring_version < ?)"]
        params: list[Any] = [scoring_version]
        if title_keywords:
            like_parts = " OR ".join(["LOWER(title) LIKE ?"] * len(title_keywords))
            where.append(f"({like_parts})")
            params.extend([f"%{k.lower()}%" for k in title_keywords])
        q = f"SELECT * FROM jobs WHERE {' AND '.join(where)}"
        return list(self.db.query(q, params))

    def iter_scored_today(self, the_date: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            """
            SELECT * FROM jobs
            WHERE final_score >= 0 AND verdict != 'reject'
              AND (first_seen_at LIKE ? OR seen_in_digest_on = '')
            ORDER BY final_score DESC
            """,
            [f"{the_date}%"],
        )
        return list(rows)

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------

    def record_run(
        self,
        started_at: str,
        fetched: int,
        new: int,
        kept: int,
        borderline: int,
        rejected: int,
        notes: str = "",
    ) -> None:
        self.db["runs"].insert(
            {
                "started_at": started_at,
                "finished_at": datetime.utcnow().isoformat(),
                "fetched": fetched,
                "new": new,
                "kept": kept,
                "borderline": borderline,
                "rejected": rejected,
                "notes": notes,
            },
            pk="started_at",
            replace=True,
        )
