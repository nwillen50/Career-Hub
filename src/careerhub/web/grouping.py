"""Group shortlist entries by place: country, then city (CH-121). Pure; no database, no page code.

Order of groups: countries (the one holding the best-ranked job first), then jobs that are remote,
then jobs whose place can't be read. Inside a country, cities follow the same rule. Within a city
the entries keep their rank order. A job with several places is listed once, under its first place;
the card still shows every place in its location line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from careerhub.modules.jobs.places import COUNTRY_NAMES, parse_places

REMOTE_GROUP = "Remote"
UNKNOWN_GROUP = "Place not stated"
NO_CITY = "City not stated"


@dataclass
class CityGroup:
    """Jobs in one city of a country (positions are 0-based ranks in the original list)."""

    name: str
    positions: list[int] = field(default_factory=list)


@dataclass
class CountryGroup:
    """Jobs in one country (or the remote and unknown groups, which have a single unnamed city)."""

    name: str
    cities: list[CityGroup] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Return how many jobs the group holds."""
        return sum(len(city.positions) for city in self.cities)


def group_by_place(locations: Sequence[str | None]) -> list[CountryGroup]:
    """Group jobs given by their location text, in rank order, into countries and cities."""
    countries: dict[str, CountryGroup] = {}
    remote = CountryGroup(REMOTE_GROUP, [CityGroup("")])
    unknown = CountryGroup(UNKNOWN_GROUP, [CityGroup("")])
    for position, text in enumerate(locations):
        places = parse_places([text or ""])
        first = places[0] if places else None
        if first is not None and first.remote:
            remote.cities[0].positions.append(position)
        elif first is None or first.country is None:
            unknown.cities[0].positions.append(position)
        else:
            name = COUNTRY_NAMES.get(first.country, first.country)
            group = countries.setdefault(name, CountryGroup(name))
            city_name = first.city or NO_CITY
            city = next((c for c in group.cities if c.name == city_name), None)
            if city is None:
                city = CityGroup(city_name)
                group.cities.append(city)
            city.positions.append(position)
    ordered = sorted(countries.values(), key=lambda g: min(min(c.positions) for c in g.cities))
    for group in ordered:
        group.cities.sort(key=lambda c: c.positions[0])
    return [*ordered, *(g for g in (remote, unknown) if g.count)]
