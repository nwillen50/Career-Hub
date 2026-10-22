"""Remove contact details and the user's name from text before it reaches a model.

Every Personal-tier prompt that includes the user's own words (profile, resume, and later the
profile-extraction step) goes through `personal_text`, so the paths can't drift apart. The model
never needs the name or a way to contact the user, so both are replaced with placeholders.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_SCHEME = re.compile(r"\b(?:mailto|tel):", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+")
# A web address with no "https://": a domain ending in a common suffix, with an optional path.
_BARE_DOMAIN = re.compile(
    r"\b(?:[\w-]+\.)+(?:com|org|net|io|dev|edu|me|ai|co)\b(?:/\S*)?", re.IGNORECASE
)
# A phone number: optional country code, then 3-3-4 digits (or 3 digits in parentheses).
_PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
# An international number written with country code and groups, like "+44 20 7946 0958".
_INTERNATIONAL = re.compile(r"(?<!\w)\+\d{1,3}(?:[\s.-]?\d{2,4}){2,4}(?!\d)")


# --- Transcripts: identity numbers and addresses (CH-109) ---------------------------------------
_SSN = re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")
_LABELLED_ID = re.compile(
    r"(?i)\b(student\s*(?:id|no\.?|number)|id\s*(?:#|no\.?|number)|sid|ssn"
    r"|social security(?: number)?)\s*[:#]?\s*[\w-]*\d[\w-]*"
)
_LABELLED_DOB = re.compile(
    r"(?i)\b(date of birth|birth\s*date|birthday|dob)\s*[:#]?\s*"
    r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4})"
)
_STREET = re.compile(
    r"(?i)\b\d{1,6}\s+(?:[\w.'-]+\s+){1,4}"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way|place|pl|"
    r"terrace|ter|circle|cir|highway|hwy)\b\.?(?:,?\s*(?:apt|unit|suite|ste|#)\s*[\w-]+)?"
)
_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
_LONG_NUMBER = re.compile(r"(?<![\w.])\d{7,}(?![\w.])")


def redact_identity_numbers(text: str) -> str:
    """Remove ID numbers, birth dates, SSN-shaped numbers, and addresses (for transcripts)."""
    text = _LABELLED_ID.sub(lambda m: f"{m.group(1)}: [id]", text)
    text = _LABELLED_DOB.sub(lambda m: f"{m.group(1)}: [birth date]", text)
    text = _SSN.sub("[id]", text)
    text = _STREET.sub("[address]", text)
    text = _STATE_ZIP.sub("[address]", text)
    return _LONG_NUMBER.sub("[id]", text)


def transcript_text(text: str, name: str, aliases: Sequence[str] = ()) -> str:
    """Prepare a transcript for a model: identity numbers and address, then contacts and name."""
    return personal_text(redact_identity_numbers(text), name, aliases)


def redact_contacts(text: str) -> str:
    """Remove email addresses, web addresses (with or without https://), and phone numbers."""
    text = _SCHEME.sub(" ", text)
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _BARE_DOMAIN.sub("[link]", text)
    return _INTERNATIONAL.sub("[phone]", _PHONE.sub("[phone]", text))


def remove_name(text: str, name: str, aliases: Sequence[str] = ()) -> str:
    """Replace the name, each part of it (3+ letters), and any aliases with `[name]`.

    Matches whole words only, so the name "Ann" leaves "Annual" alone. Aliases are nicknames and
    handles the user lists in `profile.md` (for example "Nick" or "nwillen50").
    """
    parts = [name, *name.split(), *aliases]
    wanted = {p.strip() for p in parts if len(p.strip()) >= 3}
    for part in sorted(wanted, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(part)}(?!\w)", "[name]", text, flags=re.IGNORECASE)
    return re.sub(r"\[name\](?:\s+\[name\])+", "[name]", text)  # "Nick Willen" -> one marker


def personal_text(text: str, name: str, aliases: Sequence[str] = ()) -> str:
    """Prepare the user's own text for a Personal-tier prompt: contacts first, then the name."""
    return remove_name(redact_contacts(text), name, aliases)
