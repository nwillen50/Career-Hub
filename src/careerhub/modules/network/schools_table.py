"""A reviewed table of schools that tells similar names apart (CH-179, ADR 0046).

`schools.yaml` lists official names, aliases, branch campuses, and systems of separate
universities. Matching goes through this table first; names not in it fall back to the word rules
in `schools.py`. A tie between two schools is one of four things, strongest first:

1. `same`: the same school ("Penn State" and "Pennsylvania State University").
2. `branch`: a branch campus of the same university (Penn State Behrend for Penn State).
3. `system`: a separate university in the same system (UC Davis for UCLA).
4. `none`.

An ambiguous short name (PSU) matches nothing on its own; `ambiguous_choices` says what it could
mean so the user can pick one with a school alias in preferences.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Literal

import yaml

Kind = Literal["same", "branch", "system", "none"]
RANK: dict[str, int] = {"same": 3, "branch": 2, "system": 1, "none": 0}


@dataclass(frozen=True)
class Tie:
    """How two schools are related, and the label to show for a weaker tie."""

    kind: Kind
    #: For `branch` and `system`: "Branch campus: Penn State" or "Same system: University of ...".
    label: str | None = None


@dataclass(frozen=True)
class Entry:
    """One school in the table."""

    name: str
    branch_of: str | None
    system: str | None
    #: The everyday name shown in labels: the first alias ("Penn State"), else the official name.
    short: str


def norm(text: str) -> str:
    """Lowercase, make '&' 'and', turn punctuation and hyphens into spaces, and drop 'the'."""
    words = re.findall(r"[a-z0-9]+", text.lower().replace("&", " and "))
    return " ".join(w for w in words if w != "the")


@dataclass(frozen=True)
class Table:
    """The loaded table: lookups by normalized name."""

    by_name: dict[str, Entry]
    systems: frozenset[str]
    ambiguous: dict[str, list[str]]


@cache
def load_table() -> Table:
    """Read the bundled `schools.yaml` once."""
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("careerhub.modules.network").joinpath("schools.yaml").read_text("utf-8")
    )
    by_name: dict[str, Entry] = {}
    for item in raw["schools"]:
        aliases = item.get("aliases", [])
        entry = Entry(
            item["name"],
            item.get("branch_of"),
            item.get("system"),
            aliases[0] if aliases else item["name"],
        )
        for name in (item["name"], *aliases):
            by_name.setdefault(norm(name), entry)
    return Table(
        by_name,
        frozenset(norm(s) for s in raw["systems"]),
        {norm(k): list(v) for k, v in raw["ambiguous"].items()},
    )


def ambiguous_choices(name: str) -> list[str]:
    """Return the schools a short name could mean (empty when it is not ambiguous)."""
    return load_table().ambiguous.get(norm(name), [])


def lookup(name: str) -> Entry | None:
    """Return the table entry for `name`, or None (unknown, a bare system, or ambiguous)."""
    return load_table().by_name.get(norm(name))


def _root(entry: Entry) -> str:
    """Return the university a campus belongs to (itself unless it is a branch)."""
    return entry.branch_of or entry.name


def tie_between(a: str, b: str) -> Tie | None:
    """Relate two school names using the table; None when either is not in it."""
    first, second = lookup(a), lookup(b)
    if first is None or second is None:
        return None
    if first.name == second.name:
        return Tie("same")
    if _root(first) == _root(second):
        parent = first if first.branch_of is None else second if second.branch_of is None else None
        if parent is None:  # two branches of one university: name the university by its entry
            parent = next(e for e in load_table().by_name.values() if e.name == _root(first))
        return Tie("branch", f"Branch campus: {parent.short}")
    if first.system and first.system == second.system:
        return Tie("system", f"Same system: {first.system}")
    return Tie("none")
