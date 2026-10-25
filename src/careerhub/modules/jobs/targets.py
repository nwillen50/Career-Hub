"""Target companies: the employers the user wants to follow (CH-97).

`targets.yaml` in the data folder lists companies with their sectors, an optional job-board kind
and name once known, and whether the user follows them (`enabled`). Importing a list (such as
`docs/seeds/engine-resident-companies.yaml`) adds new companies **disabled** and never changes a
company the user already has. `match_interests` finds companies whose sectors match the interests in
`profile.md`; enabling them is the user's call. Nothing here fetches anything.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from careerhub.core.files import write_private
from careerhub.core.names import company_key
from careerhub.core.paths import data_dir

TARGETS_FILENAME = "targets.yaml"
_WORDS = re.compile(r"[a-z0-9]+")
#: Words that carry no meaning when matching an interest to a sector.
_STOP = frozenset({"and", "of", "the", "for", "systems", "tech", "technology"})


class TargetsError(Exception):
    """A targets file can't be read. The message names the problem, never the file's contents."""


class Target(BaseModel):
    """One company the user may follow."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    sectors: list[str] = Field(default_factory=list)
    #: Where its jobs are listed, once known: "greenhouse", "lever", or "ashby".
    board_kind: Literal["greenhouse", "lever", "ashby"] | None = None
    board: str | None = Field(default=None, max_length=100)
    enabled: bool = False
    #: A job system we can't read (for example "Workday"); shown so the gap is visible.
    unsupported_ats: str | None = Field(default=None, max_length=50)
    #: The day `enable-matching` switched this company on (YYYY-MM-DD), so that run can be undone.
    matched_on: str | None = Field(default=None, max_length=10)


class TargetsFile(BaseModel):
    """The contents of a targets file."""

    model_config = ConfigDict(extra="forbid")

    targets: list[Target] = Field(default_factory=list)


def targets_path() -> Path:
    """Return where `targets.yaml` lives (it may not exist)."""
    return data_dir() / TARGETS_FILENAME


def read_targets(path: Path) -> list[Target]:
    """Read and validate a targets file (also used for seed lists)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return TargetsFile.model_validate(raw).targets
    except OSError:
        raise TargetsError(f"Can't read {path.name}.") from None
    except yaml.YAMLError:
        raise TargetsError(f"{path.name} is not valid YAML.") from None
    except ValidationError as error:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in error.errors()})
        raise TargetsError(
            f"{path.name} has invalid entries (fields: {', '.join(fields)})."
        ) from None


def load_targets(path: Path | None = None) -> list[Target]:
    """Load the user's targets; no file means no targets."""
    path = path or targets_path()
    return read_targets(path) if path.exists() else []


def save_targets(targets: list[Target], path: Path | None = None) -> Path:
    """Write the targets file, readable only by the user."""
    path = path or targets_path()
    data = TargetsFile(targets=targets).model_dump(mode="json", exclude_none=True)
    write_private(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
    return path


def merge_import(existing: list[Target], incoming: list[Target]) -> tuple[list[Target], int]:
    """Add companies not already followed (matched by company key), disabled; return the count.

    A company the user already has is left exactly as it is, and repeats inside `incoming` count
    once, so importing the same list twice changes nothing.
    """
    merged = list(existing)
    seen = {company_key(t.name) for t in existing}
    added = 0
    for target in incoming:
        key = company_key(target.name)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(target.model_copy(update={"enabled": False}))
        added += 1
    return merged, added


def _tokens(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if w not in _STOP}


def match_reason(target: Target, interests: list[str]) -> str | None:
    """Say why `target` matches an interest, or None when it does not (CH-215).

    The company's first-listed sector is its primary one: an interest matches it when all of the
    interest's words are in it. A later sector counts only when the interest names it
    specifically: the same words, or at least two words that are all in it. So the single word
    "AI" matches a company whose main sector is applied AI, not every biotech that mentions AI.
    """
    for interest in interests:
        wanted = _tokens(interest)
        if not wanted:
            continue
        for position, sector in enumerate(target.sectors):
            words = _tokens(sector)
            if position == 0 and wanted <= words:
                return f"{interest.strip()} (main sector: {sector})"
            if position > 0 and (wanted == words or (len(wanted) >= 2 and wanted <= words)):
                return f"{interest.strip()} (also: {sector})"
    return None


def matches_interests(target: Target, interests: list[str]) -> bool:
    """Say whether the company's main sector (or a sector named specifically) fits an interest."""
    return match_reason(target, interests) is not None


def set_unsupported(targets: list[Target], name: str, ats: str) -> bool:
    """Record that `name` uses a job system we can't read; return True if no target matches."""
    for target in targets:
        if company_key(target.name) == company_key(name):
            target.unsupported_ats = " ".join(ats.split())[:50] or None
            return False
    return True


def set_enabled(targets: list[Target], names: list[str], enabled: bool) -> list[str]:
    """Enable or disable the named companies; return the names that matched no target."""
    by_key = {company_key(t.name): t for t in targets}
    unknown = []
    for name in names:
        target = by_key.get(company_key(name))
        if target is None:
            unknown.append(name)
        else:
            target.enabled = enabled
            target.matched_on = None  # the user's own choice now, not the matcher's
    return unknown
