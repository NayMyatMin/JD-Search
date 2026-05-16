"""Scraper base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import JobPost


class Scraper(ABC):
    """Fetches raw job postings from one source.

    Each adapter returns plain JobPost objects with best-effort plaintext
    descriptions. Extraction, gating and scoring happen downstream.
    """

    name: str = "base"

    @abstractmethod
    def fetch(self) -> list[JobPost]: ...
