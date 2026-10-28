"""Read a job's location text into city, region, and country (CH-120). No model call, no network.

`parse_place("Munich, DE")` gives `Place(city="Munich", region=None, country="DE")`. Countries are
ISO 3166 two-letter codes. Whatever cannot be read with confidence keeps its original text and an
unknown country: this never guesses. "DE", "CA" and similar codes are both a country and a US state
or Canadian province; a code after a city is read as a state unless the city is a well-known city
elsewhere ("Munich, DE" is Germany, "Wilmington, DE" is Delaware).

The original text is always kept in `Place.text`; nothing is stored or changed on the job.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

#: ISO code -> common names (lowercase). The first name is shown to people.
COUNTRIES: dict[str, tuple[str, ...]] = {
    "US": ("united states", "usa", "u.s.a.", "u.s.", "us", "america", "united states of america"),
    "CA": ("canada",),
    "GB": ("united kingdom", "uk", "u.k.", "great britain", "england", "scotland", "wales", "gb"),
    "IE": ("ireland",),
    "DE": ("germany", "deutschland"),
    "FR": ("france",),
    "ES": ("spain", "espana", "españa"),
    "IT": ("italy", "italia"),
    "NL": ("netherlands", "the netherlands", "holland"),
    "BE": ("belgium",),
    "CH": ("switzerland",),
    "AT": ("austria", "österreich"),
    "SE": ("sweden",),
    "NO": ("norway",),
    "DK": ("denmark",),
    "FI": ("finland",),
    "PL": ("poland",),
    "PT": ("portugal",),
    "CZ": ("czech republic", "czechia"),
    "RO": ("romania",),
    "UA": ("ukraine",),
    "EE": ("estonia",),
    "LT": ("lithuania",),
    "LV": ("latvia",),
    "GR": ("greece",),
    "TR": ("turkey", "türkiye"),
    "IL": ("israel",),
    "AE": ("united arab emirates", "uae"),
    "SA": ("saudi arabia",),
    "EG": ("egypt",),
    "ZA": ("south africa",),
    "NG": ("nigeria",),
    "KE": ("kenya",),
    "IN": ("india",),
    "PK": ("pakistan",),
    "BD": ("bangladesh",),
    "CN": ("china",),
    "HK": ("hong kong",),
    "TW": ("taiwan",),
    "JP": ("japan",),
    "KR": ("south korea", "korea"),
    "SG": ("singapore",),
    "MY": ("malaysia",),
    "TH": ("thailand",),
    "VN": ("vietnam",),
    "ID": ("indonesia",),
    "PH": ("philippines",),
    "AU": ("australia",),
    "NZ": ("new zealand",),
    "MX": ("mexico", "méxico"),
    "BR": ("brazil", "brasil"),
    "AR": ("argentina",),
    "CL": ("chile",),
    "CO": ("colombia",),
    "PE": ("peru",),
}
#: Code -> display name.
COUNTRY_NAMES = {code: names[0].title() for code, names in COUNTRIES.items()}
COUNTRY_NAMES.update({"US": "United States", "GB": "United Kingdom", "AE": "United Arab Emirates"})
_BY_NAME = {name: code for code, names in COUNTRIES.items() for name in names if name != "us"}

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}  # fmt: skip
CA_PROVINCES = {
    "AB": "alberta", "BC": "british columbia", "MB": "manitoba", "NB": "new brunswick",
    "NL": "newfoundland and labrador", "NS": "nova scotia", "NT": "northwest territories",
    "NU": "nunavut", "ON": "ontario", "PE": "prince edward island", "QC": "quebec",
    "SK": "saskatchewan", "YT": "yukon",
}  # fmt: skip
#: Well-known cities outside the US, to read "Munich, DE" as Germany rather than Delaware.
FOREIGN_CITIES = {
    "berlin": "DE", "munich": "DE", "hamburg": "DE", "frankfurt": "DE", "cologne": "DE",
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "dublin": "IE", "paris": "FR",
    "madrid": "ES", "barcelona": "ES", "amsterdam": "NL", "zurich": "CH", "stockholm": "SE",
    "copenhagen": "DK", "oslo": "NO", "helsinki": "FI", "warsaw": "PL", "tel aviv": "IL",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "bangalore": "IN", "bengaluru": "IN",
    "mumbai": "IN", "hyderabad": "IN", "tokyo": "JP", "singapore": "SG", "sydney": "AU",
    "melbourne": "AU", "sao paulo": "BR", "mexico city": "MX", "dubai": "AE", "seoul": "KR",
}  # fmt: skip
#: Regions bigger than a country, kept as the region with no country.
REGIONS = {
    "eu": "EU", "europe": "Europe", "emea": "EMEA", "apac": "APAC", "latam": "LATAM",
    "north america": "North America", "americas": "Americas", "worldwide": "Worldwide",
    "global": "Worldwide", "anywhere": "Worldwide",
}  # fmt: skip

_SPLIT_PLACES = re.compile(r"\s*(?:;|\||\n| / | or )\s*")
_REMOTE = re.compile(r"(?i)\b(remote|work from home|wfh|distributed)\b")


@dataclass(frozen=True)
class Place:
    """One place. `text` is the original; `country` is an ISO code or None when unknown."""

    text: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    remote: bool = False


def _clean(text: str) -> str:
    return " ".join(text.replace(" ", " ").split())


def _country_of(part: str) -> str | None:
    """Return the ISO code if `part` is a country name or a two-letter country code."""
    low = part.lower().strip(". ")
    if low in _BY_NAME:
        return _BY_NAME[low]
    if len(low) == 2 and low.upper() in COUNTRIES:
        return low.upper()
    return None


def _state_code(part: str) -> str | None:
    """Return the US state code for a state name or abbreviation, else None."""
    low = part.lower().strip(". ")
    for code, name in US_STATES.items():
        if low == name or part.strip(". ") == code:
            return code
    return None


def _province_code(part: str) -> str | None:
    """Return the Canadian province code for a name or abbreviation, else None."""
    low = part.lower().strip(". ")
    for code, name in CA_PROVINCES.items():
        if low == name or part.strip(". ") == code:
            return code
    return None


def parse_place(text: str) -> Place:
    """Read one location string. Unreadable parts are left out, never guessed."""
    original = _clean(text)
    if not original:
        return Place(original)
    remote = bool(_REMOTE.search(original))
    rest = _REMOTE.sub(" ", original)
    rest = re.sub(r"[()\[\]]|\s[-–—]\s|^[-–—\s,]+|[-–—\s,]+$", ",", rest)
    parts = [p.strip() for p in re.split(r",", rest) if p.strip()]
    if remote:
        return _parse_remote(original, parts)
    if not parts:
        return Place(original)
    # Region wording alone ("Europe", "EMEA") is a region without a country.
    if len(parts) == 1 and parts[0].lower() in REGIONS:
        return Place(original, region=REGIONS[parts[0].lower()])
    country = _country_of(parts[-1]) if len(parts) > 1 or len(parts[-1]) > 2 else None
    if len(parts) == 1:
        only = parts[0]
        if country:
            return Place(original, country=country)
        if (state := _state_code(only)) is not None and len(only) > 2:
            return Place(original, region=state, country="US")
        return Place(original, city=only if only.lower() not in REGIONS else None)
    city, last = parts[0], parts[-1]
    ambiguous = len(last) == 2 and last.upper() in COUNTRIES
    if ambiguous:
        foreign = FOREIGN_CITIES.get(city.lower())
        state, province = _state_code(last), _province_code(last)
        if foreign == last.upper() or (state is None and province is None):
            return Place(original, city=city, country=last.upper())
        if state and not province:
            return Place(original, city=city, region=state, country="US")
        return Place(original, city=city, region=province or state, country=None)
    if country:
        region = parts[1] if len(parts) > 2 else None
        return Place(original, city=city, region=region, country=country)
    state, province = _state_code(last), _province_code(last)
    if state:
        return Place(original, city=city, region=state, country="US")
    if province:
        return Place(original, city=city, region=province, country="CA")
    return Place(original, city=city)


def _parse_remote(original: str, parts: list[str]) -> Place:
    """Read "Remote", "Remote - EU", "Remote (Germany)", "Remote, US"."""
    for part in parts:
        if part.lower() in REGIONS:
            return Place(original, region=REGIONS[part.lower()], remote=True)
        code = _country_of(part)
        if code:
            return Place(original, country=code, remote=True)
        if (state := _state_code(part)) is not None and len(part) > 2:
            return Place(original, region=state, country="US", remote=True)
    return Place(original, remote=True)


def parse_places(texts: Iterable[str]) -> list[Place]:
    """Read several location strings; a string listing several places ("A; B") is split first."""
    places = []
    for text in texts:
        for piece in _SPLIT_PLACES.split(text or ""):
            if piece.strip():
                places.append(parse_place(piece))
    return places


def fold(text: str) -> str:
    """Lowercase `text` and drop accents, so "Zürich" and "Zurich" compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def country_matches(wanted: str, places: Iterable[Place]) -> bool:
    """Say whether `wanted` ("Germany", "DE", "Munich, Germany") names a country some place is in.

    Only a wanted place that reads as a country (or a city plus its country) can match here; the
    caller still tries the older city matching. Remote places count for the country they name.
    """
    target = parse_place(wanted)
    if target.country is None:
        return False
    for place in places:
        if place.country != target.country:
            continue
        if target.city is not None and fold(place.city or "") != fold(target.city):
            continue
        if target.region is not None and place.region != target.region:
            continue
        return True
    return False
