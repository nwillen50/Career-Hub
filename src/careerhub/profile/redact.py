"""Remove contact details, home addresses, and the user's name from text before it reaches a model.

Every Personal-tier prompt that includes the user's own words (profile, resume, and later the
profile-extraction step) goes through `personal_text`, so the paths can't drift apart. The model
never needs the name or a way to contact the user, so both are replaced with placeholders.

The text is normalized first (Unicode NFKC, invisible characters removed, odd dashes made plain) so
that "Zoë" written with a combining accent, a name split by a soft hyphen, or a phone number with
en dashes cannot slip past the patterns. Patterns are written to run in linear time: they start
only at the beginning of a token, and `personal_text` cuts absurdly long input first.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

#: Longer input is cut before redaction (callers cut much shorter afterwards), bounding the work.
MAX_INPUT_CHARS = 100_000
_INVISIBLE = re.compile("[­​-‏⁠﻿]")
_DASHES = re.compile("[‐-―−﹘﹣－]")

_SCHEME = re.compile(r"\b(?:mailto|tel):", re.IGNORECASE)
# The local part may not start in the middle of a token, which keeps the scan linear.
_EMAIL = re.compile(r"(?<![\w.+'’-])[\w.+'’-]+@[\w-]+(?:\.[\w-]+)+")
# "name at example dot com", "name (at) example (dot) com", "name [at] example [dot] com".
_SPOKEN_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.+-]+\s*(?:\(at\)|\[at\]|\sat\s)\s*[\w-]+"
    r"(?:\s*(?:\(dot\)|\[dot\]|\sdot\s)\s*[\w-]+)+(?![\w])",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:h(?:tt|xx)ps?://|www\.)\S+", re.IGNORECASE)
# A web address with no "https://": a domain ending in a suffix, with an optional path.
_TLD = (
    r"co\.uk|org\.uk|ac\.uk|com\.au|co\.nz|co\.jp|com\.br|co\.in|"
    r"com|org|net|io|dev|edu|me|ai|co|xyz|app|page|tech|us|uk|ly|info|biz|site|online|blog|cloud|"
    r"design|work|space|link|gg|cc|tv|fm|ws|ca|de|fr|in|au|nz"
)
_BARE_DOMAIN = re.compile(rf"(?<![\w.-])(?:[\w-]+\.)+(?:{_TLD})\b(?:/\S*)?", re.IGNORECASE)
#: Tech names that look like web addresses and must stay (skills lists).
_NOT_LINKS = frozenset({"asp.net", "vb.net", "socket.io", "node.js", "next.js", "vue.js"})
# A phone number: optional country code, then 3-3-4 digits (or 3 digits in parentheses).
_PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s./-]?)?(?:\(\d{3}\)|\d{3})[\s./-]?\d{3}[\s./-]?\d{4}(?!\d)"
)
# An international number written with country code and groups, like "+44 (0)20 7946 0958".
_INTERNATIONAL = re.compile(r"(?<!\w)\+\d{1,3}(?:[\s.-]?\(0\))?(?:[\s.-]?\d{2,4}){2,4}(?!\d)")
# A UK-style national number ("07911 123456") and digits typed one by one ("6 1 7 5 5 5 0 1 4 2").
_NATIONAL = re.compile(r"(?<!\d)0\d{3,4}\s\d{5,6}(?!\d)")
_SPACED_DIGITS = re.compile(r"(?<!\d)(?:\d[\s.-]){8,}\d(?!\d)")
# A home address: a number, capitalized words, then a street-type word; or a state and ZIP code.
_STREET = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][\w.'-]*\s+){1,4}"
    r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way|place|pl|"
    r"terrace|ter|circle|cir|highway|hwy)\b\.?(?:,?\s*(?i:apt|unit|suite|ste|#)\s*[\w-]+)?"
)
_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")


def normalize(text: str) -> str:
    """Make text comparable: NFKC, no invisible characters, one kind of dash."""
    text = unicodedata.normalize("NFKC", text)
    return _DASHES.sub("-", _INVISIBLE.sub("", text))


def _link_or_keep(match: re.Match[str]) -> str:
    return match.group(0) if match.group(0).lower() in _NOT_LINKS else "[link]"


def redact_address(text: str) -> str:
    """Remove street addresses and state-plus-ZIP endings."""
    return _STATE_ZIP.sub("[address]", _STREET.sub("[address]", text))


def redact_contacts(text: str) -> str:
    """Remove email addresses, web addresses (with or without https://), and phone numbers."""
    text = normalize(text)
    text = _SCHEME.sub(" ", text)
    text = _EMAIL.sub("[email]", text)
    text = _SPOKEN_EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _BARE_DOMAIN.sub(_link_or_keep, text)
    for pattern in (_PHONE, _INTERNATIONAL, _NATIONAL, _SPACED_DIGITS):
        text = pattern.sub("[phone]", text)
    return text


def _letters_pattern(part: str) -> str:
    """Match `part` even when its letters are spread out ("W I L L E N" or "W.I.L.L.E.N")."""
    gap = r"[\s.\-]?" if part.isalpha() and len(part) <= 20 else ""
    return gap.join(re.escape(ch) for ch in part)


def remove_name(text: str, name: str, aliases: Sequence[str] = ()) -> str:
    """Replace the name, each part of it, and any aliases with `[name]`.

    Parts are split on spaces and hyphens. Matches whole words only (the name "Ann" leaves
    "Annual" alone), ignoring case, accents written as combining marks, and letter spacing.
    A part shorter than 3 letters ("Wu") is removed only next to a longer part of the name
    ("Wu, Wei"). Aliases are nicknames and handles listed in `profile.md`.
    """
    text = normalize(text)
    name = normalize(name)
    pieces = [p for p in re.split(r"[\s\-]+", name) if p]
    long_parts = {p for p in [name.strip(), *pieces] if len(p) >= 3}
    long_parts |= {a.strip() for a in map(normalize, aliases) if len(a.strip()) >= 3}
    short_parts = {p for p in pieces if len(p) < 3 and p.isalpha()}
    for short in short_parts:  # "Wu, Wei" and "Wei Wu"
        for long in sorted({p for p in pieces if len(p) >= 3}, key=len, reverse=True):
            both = rf"(?:{_letters_pattern(short)}[,\s]+{_letters_pattern(long)}|"
            both += rf"{_letters_pattern(long)}[,\s]+{_letters_pattern(short)})"
            text = re.sub(rf"(?<!\w){both}(?!\w)", "[name]", text, flags=re.IGNORECASE)
    for part in sorted(long_parts, key=len, reverse=True):
        text = re.sub(
            rf"(?<!\w){_letters_pattern(part)}(?!\w)", "[name]", text, flags=re.IGNORECASE
        )
    return re.sub(r"\[name\](?:\s+\[name\])+", "[name]", text)  # "Nick Willen" -> one marker


def personal_text(text: str, name: str, aliases: Sequence[str] = ()) -> str:
    """Prepare the user's own text for a Personal-tier prompt.

    Order: normalize and cut to a safe length, then addresses and contacts, then the name.
    """
    text = normalize(text)[:MAX_INPUT_CHARS]
    return remove_name(redact_contacts(redact_address(text)), name, aliases)
