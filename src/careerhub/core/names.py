"""Name matching helpers shared by every importer (CH-86).

`company_key` makes "Microsoft", "Microsoft.com", and "Microsoft Corporation" one company, without
any fuzzy matching that could merge two real ones. A short built-in list maps names that differ
completely (Google and Alphabet) to one key.
"""

from __future__ import annotations

import re

#: Words dropped from the end of a name, repeatedly ("Acme Holdings Inc." keeps "holdings").
LEGAL_WORDS = frozenset(
    "inc incorporated corp corporation co company llc ltd limited plc gmbh ag sa lp llp".split()
)
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


def company_key(name: str) -> str:
    """Return the key that identifies a company however its name is written.

    Lowercase; "&" becomes "and"; a leading "www." and "the" and a trailing web suffix (.com, .io,
    .ai, .co, .net, .org) and legal words (Inc, Corp, Corporation, LLC, Ltd...) are dropped;
    apostrophes and dots disappear and other punctuation becomes a space.
    """
    words = [w for w in re.split(r"[\s,]+", name.lower().replace("&", " and ").strip()) if w]
    if words and words[0].startswith("www."):
        words[0] = words[0][4:]
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    while words:
        last = words[-1].rstrip(".")
        if len(words) > 1 and last in LEGAL_WORDS:
            words.pop()
        elif _WEB_SUFFIX.search(last) and last != _WEB_SUFFIX.sub("", last):
            words[-1] = _WEB_SUFFIX.sub("", last)
        else:
            break
    text = re.sub(r"['’.]", "", " ".join(words))
    key = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()
    return BUILT_IN_ALIASES.get(key, key)
