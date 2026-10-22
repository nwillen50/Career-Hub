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
