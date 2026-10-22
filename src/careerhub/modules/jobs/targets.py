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


def matches_interests(target: Target, interests: list[str]) -> bool:
    """Say whether any of the company's sectors covers any interest (all of its words match)."""
    sector_words = [_tokens(s) for s in target.sectors]
    return any(
        (wanted := _tokens(interest)) and any(wanted <= words for words in sector_words)
        for interest in interests
    )


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
    return unknown
