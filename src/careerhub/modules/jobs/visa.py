"""Work-visa flags for jobs abroad (CH-122). Advice only.

A job in a country where the user cannot already work (`work_rights` in preferences.yaml) gets a
flag in words: "Needs a work visa for Germany: sponsors visas", "...: won't sponsor", or
"...: sponsorship not stated". A remote job open to a country the user can work in gets no flag,
and neither does a place whose country can't be read. If `work_rights` is not set, every country
gets the flag, because Career Hub can't tell which are the user's own. Career Hub never says which
visa the user qualifies for and links nothing: this is a pointer to check, not legal guidance.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from careerhub.modules.jobs.extract import JobFields
from careerhub.modules.jobs.places import COUNTRY_NAMES, Place, parse_places


@dataclass(frozen=True)
class VisaFlag:
    """The flag text, and whether the posting itself rules out someone without the right to work."""

    text: str
    #: True only when the posting says it won't sponsor or needs an existing right to work.
    posting_refuses: bool


def foreign_countries(places: list[Place], work_rights: Collection[str] | None) -> list[str]:
    """Return the countries of `places` where the user cannot already work (empty if one is fine).

    If any place is somewhere the user can work, nothing is foreign: they could take that one.
    """
    rights = set(work_rights or [])
    countries: list[str] = []
    for place in places:
        if place.country is None:
            continue  # unreadable: never guessed
        if place.country in rights:
            return []
        if place.country not in countries:
            countries.append(place.country)
    return countries


def visa_flag(
    fields: JobFields | None, location: str | None, work_rights: Collection[str] | None
) -> VisaFlag | None:
    """Return the flag for a job, or None when it needs none."""
    texts = list(fields.locations) if fields and fields.locations else [location or ""]
    countries = foreign_countries(parse_places(texts), work_rights)
    if not countries:
        return None
    names = " or ".join(COUNTRY_NAMES.get(code, code) for code in countries)
    sponsorship = fields.visa_sponsorship if fields else "unknown"
    needs_right = bool(fields and fields.work_authorization == "local_right_to_work_required")
    if needs_right:
        detail = "requires an existing right to work there"
    elif sponsorship == "yes":
        detail = "sponsors visas"
    elif sponsorship == "no":
        detail = "won't sponsor"
    else:
        detail = "sponsorship not stated"
    return VisaFlag(f"Needs a work visa for {names}: {detail}", needs_right or sponsorship == "no")
