"""Name matching helpers shared by every importer (CH-86).

`company_key` makes "Microsoft", "Microsoft.com", and "Microsoft Corporation" one company, without
any fuzzy matching that could merge two real ones. A short built-in list maps names that differ
completely (Google and Alphabet) to one key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: Words dropped from the end of a name, repeatedly ("Acme Holdings Inc." keeps "holdings").
LEGAL_WORDS = frozenset(
    "inc incorporated corp corporation co company llc ltd limited plc gmbh ag sa lp llp".split()
)
#: A key shorter than this after dropping endings keeps the endings (so "X.ai" and "X Corp" differ).
MIN_KEY_LENGTH = 3
_WEB_SUFFIX = re.compile(r"\.(?:com|io|ai|co|net|org)$")
#: Names that differ completely but are one employer. Keys and values are already normalized.
BUILT_IN_ALIASES = {
    "alphabet": "google",
    "facebook": "meta",
    "meta platforms": "meta",
    "aws": "amazon web services",
    "walt disney": "disney",
    "international business machines": "ibm",
}


def company_key(name: str, aliases: Mapping[str, str] | None = None) -> str:
    """Return the key that identifies a company however its name is written.

    Lowercase; "&" becomes "and"; a leading "www." and "the" and a trailing web suffix (.com, .io,
    .ai, .co, .net, .org) and legal words (Inc, Corp, Corporation, LLC, Ltd...) are dropped;
    apostrophes and dots disappear and other punctuation becomes a space. `aliases` are the user's
    own, already checked by `resolve_aliases`, and are applied last.
    """
    words = [w for w in re.split(r"[\s,]+", name.lower().replace("&", " and ").strip()) if w]
    if words and words[0].startswith("www."):
        words[0] = words[0][4:]
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    unstripped = list(words)
    while words:
        last = words[-1].rstrip(".")
        if len(words) > 1 and last in LEGAL_WORDS:
            words.pop()
        elif _WEB_SUFFIX.search(last) and last != _WEB_SUFFIX.sub("", last):
            words[-1] = _WEB_SUFFIX.sub("", last)
        else:
            break
    key = _tidy(words)
    if len(key) < MIN_KEY_LENGTH and words != unstripped:
        key = _tidy(unstripped)  # "X.ai" and "X Corp" would both shrink to "x": keep their endings
    key = BUILT_IN_ALIASES.get(key, key)
    return aliases.get(key, key) if aliases else key


def _tidy(words: list[str]) -> str:
    """Join `words`, drop apostrophes and dots, and turn other punctuation into single spaces."""
    text = re.sub(r"['’.]", "", " ".join(words))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def resolve_aliases(raw: Mapping[str, str]) -> dict[str, str]:
    """Check the user's alias list ("old name" -> "name it is now") and return it as keys.

    Chains are followed to the end (A -> B and B -> C make A -> C). A loop (A -> B -> A) or a blank
    name raises `ValueError`, because no single company would be left to use.
    """
    pairs: dict[str, str] = {}
    for alias, target in raw.items():
        a, t = company_key(alias), company_key(target)
        if not a or not t:
            raise ValueError(f"alias {alias!r} -> {target!r} has a blank name")
        if a != t:
            pairs[a] = t
    resolved: dict[str, str] = {}
    for start in pairs:
        seen, here = {start}, pairs[start]
        while here in pairs:
            if here in seen:
                raise ValueError(f"company aliases loop back to {here!r}")
            seen.add(here)
            here = pairs[here]
        resolved[start] = here
    return resolved
