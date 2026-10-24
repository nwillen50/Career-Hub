"""Schools: what the user's contacts attended, and who shares the user's school (CH-101).

Career Hub only knows a person's school when something says so: an import with a school column, or
the user tagging them. LinkedIn's connections export has no schools and nothing here scrapes
LinkedIn. `school_key` makes "Tufts", "Tufts University", and "Tufts Univ." one school, with a short
alias list for common abbreviations (MIT, UMass...) and the user's own aliases.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from urllib.parse import quote

from sqlalchemy.orm import Session

from careerhub.modules.network.schools_table import RANK, Tie, ambiguous_choices, tie_between
from careerhub.profile.facts import accepted_facts
from careerhub.profile.files import ProfileFileError, load_preferences, load_profile_doc

# "College" is kept: Boston College and Boston University are different schools.
_NOISE = frozenset({"university", "univ", "the", "of", "at", "in", "and"})
#: Abbreviations and short names, as school keys (already normalized).
BUILT_IN_ALIASES = {
    "mit": "massachusetts institute technology",
    "umass": "massachusetts",
    "umich": "michigan",
    "ucla": "california los angeles",
    "uc berkeley": "california berkeley",
    "berkeley": "california berkeley",
    "ucsd": "california san diego",
    "nyu": "new york",
    "gatech": "georgia institute technology",
    "georgia tech": "georgia institute technology",
    "cmu": "carnegie mellon",
    "caltech": "california institute technology",
    "rpi": "rensselaer polytechnic institute",
}


#: Places that have both "University of X" and a different "X University" (CH-155); for these the
#: "X University" form keeps the word, so the two schools do not become one.
_SHARED_NAMES = frozenset({"washington", "miami", "portland", "richmond", "charleston", "dayton"})


def school_key(name: str, aliases: dict[str, list[str]] | None = None) -> str:
    """Return the key that identifies a school however its name is written."""
    raw = [w for w in re.findall(r"[a-z0-9]+", name.lower())]
    words = [w for w in raw if w not in _NOISE]
    key = " ".join(words)
    lead = [w for w in raw if w != "the"]
    word_last = lead[:1] not in (["university"], ["univ"]) and (
        "university" in lead or "univ" in lead
    )
    if key in _SHARED_NAMES and word_last:
        key = f"{key} university"  # "Washington University", not "(The) University of Washington"
    for canonical, names in (aliases or {}).items():
        if key in {school_key(n) for n in [canonical, *names]}:
            return school_key(canonical)
    return BUILT_IN_ALIASES.get(key, key)


def _user_name(name: str, aliases: dict[str, list[str]] | None) -> str:
    """Return the user's chosen full name when `name` is one of their aliases, else `name`."""
    for canonical, names in (aliases or {}).items():
        if school_key(name) in {school_key(n) for n in [canonical, *names]}:
            return canonical
    return name


def _is_same(a: str, b: str, aliases: dict[str, list[str]] | None) -> bool:
    """Say whether two names are one school: the user's aliases win, then the table, then words."""
    a, b = _user_name(a, aliases), _user_name(b, aliases)
    key_a, key_b = school_key(a, aliases), school_key(b, aliases)
    if key_a == key_b and school_key(a) != school_key(b):
        return True  # equal only because of the user's own school alias
    tie = tie_between(a, b)
    if tie is not None:
        return tie.kind == "same"
    if ambiguous_choices(a) or ambiguous_choices(b):
        return False  # "PSU" means several schools: it matches nothing, not even itself
    return key_a == key_b


def same_school(
    schools: Iterable[str], mine: Sequence[str], aliases: dict[str, list[str]] | None = None
) -> str | None:
    """Return the first of `schools` that is one of `mine` (as written by the person), else None."""
    for school in schools:
        if any(school_key(m, aliases) and _is_same(school, m, aliases) for m in mine):
            return school
    return None


def related_school(
    schools: Iterable[str], mine: Sequence[str], aliases: dict[str, list[str]] | None = None
) -> Tie | None:
    """Return the strongest weaker tie (branch campus, then same system) between the two lists.

    Only meaningful when `same_school` found nothing. The label reads "Branch campus: Penn State"
    or "Same system: University of California".
    """
    best: Tie | None = None
    for school in schools:
        for m in mine:
            if _is_same(school, m, aliases):
                continue
            tie = tie_between(_user_name(school, aliases), _user_name(m, aliases))
            if tie and RANK[tie.kind] > RANK[best.kind if best else "none"]:
                best = tie
    return best if best and best.kind in ("branch", "system") else None


def my_schools(session: Session | None) -> list[str]:
    """Return the user's schools from `profile.md` and accepted resume and transcript facts."""
    try:
        doc = load_profile_doc()
    except ProfileFileError:
        return []
    names = [e.school for e in doc.education] if doc else []
    facts = accepted_facts(session) if session is not None else None
    if facts:
        names += [e.school for e in facts.education]
    return list(dict.fromkeys(n for n in names if n.strip()))


def my_school_aliases() -> dict[str, list[str]]:
    """Return the user's own school aliases from `preferences.yaml`."""
    try:
        preferences = load_preferences()
    except ProfileFileError:
        return {}
    return preferences.school_aliases if preferences else {}


def alumni_search_link(school_slug: str | None, company_name: str) -> str | None:
    """Build (never fetch) a LinkedIn alumni search link: the school's People tab for a company.

    Returns None without a configured `linkedin_school_slug` or with an unsafe one.
    """
    if not school_slug or not re.fullmatch(r"[A-Za-z0-9-]+", school_slug):
        return None
    keyword = quote(company_name.strip(), safe="")
    return f"https://www.linkedin.com/school/{school_slug}/people/?keywords={keyword}"
