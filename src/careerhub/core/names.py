"""Name matching helpers shared by every importer."""

from __future__ import annotations

import re


def company_key(name: str) -> str:
    """Return the lowercase, single-spaced form of a company name used to avoid duplicates."""
    return re.sub(r"\s+", " ", name).strip().lower()
