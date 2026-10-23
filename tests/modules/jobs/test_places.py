"""Reading location text into city, region, and country (CH-120). Saved samples, no network."""

from __future__ import annotations

import pytest

from careerhub.modules.jobs.filters import _location_ok
from careerhub.modules.jobs.places import Place, country_matches, parse_place, parse_places
from careerhub.profile.models import HardConstraints


def read(text: str) -> tuple[str | None, str | None, str | None, bool]:
    place = parse_place(text)
    return place.city, place.region, place.country, place.remote


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Boston, MA", ("Boston", "MA", "US", False)),
        ("Austin, Texas", ("Austin", "TX", "US", False)),
        ("Texas", (None, "TX", "US", False)),
        ("Toronto, ON", ("Toronto", "ON", "CA", False)),
        ("Vancouver, BC, Canada", ("Vancouver", "BC", "CA", False)),
        ("Berlin, Germany", ("Berlin", None, "DE", False)),
        ("Paris, France", ("Paris", None, "FR", False)),
        ("London, UK", ("London", None, "GB", False)),
        ("Munich, DE", ("Munich", None, "DE", False)),
        ("Wilmington, DE", ("Wilmington", "DE", "US", False)),
        ("Palo Alto, CA", ("Palo Alto", "CA", "US", False)),
        ("Toronto, CA", ("Toronto", None, "CA", False)),
        ("Deutschland", (None, None, "DE", False)),
        ("Germany", (None, None, "DE", False)),
        ("Remote", (None, None, None, True)),
        ("Remote - EU", (None, "EU", None, True)),
        ("Remote (Germany)", (None, None, "DE", True)),
        ("Remote, US", (None, None, "US", True)),
        ("Europe", (None, "Europe", None, False)),
    ],
)
def test_common_location_shapes(text: str, expected: tuple[object, ...]) -> None:
    assert read(text) == expected


def test_unreadable_text_is_kept_and_the_country_stays_unknown() -> None:
    place = parse_place("Building 7, Campus Drive")
    assert place.country is None and place.text == "Building 7, Campus Drive"
    assert parse_place("").text == "" and parse_place("   ") == Place("")


def test_several_places_in_one_string_are_split() -> None:
    places = parse_places(["New York, NY; San Francisco, CA", "Remote | Berlin, Germany"])
    assert [p.city for p in places] == ["New York", "San Francisco", None, "Berlin"]
    assert [p.country for p in places] == ["US", "US", None, "DE"]


def test_the_original_text_is_always_kept() -> None:
    assert parse_place("  Berlin,   Germany ").text == "Berlin, Germany"


@pytest.mark.parametrize("wanted", ["Germany", "Berlin, Germany", "Deutschland"])
def test_a_country_preference_matches_cities_in_it(wanted: str) -> None:
    assert country_matches(wanted, parse_places(["Berlin, Germany"]))
    assert country_matches(wanted, parse_places(["Munich, DE"])) or wanted == "Berlin, Germany"


def test_a_state_or_city_preference_is_not_widened_to_the_whole_country() -> None:
    assert country_matches("Washington", parse_places(["Seattle, WA"]))
    assert not country_matches("Washington", parse_places(["Boston, MA"]))
    assert not country_matches("Berlin, Germany", parse_places(["Munich, Germany"]))
    assert not country_matches("Germany", parse_places(["Paris, France"]))
    assert not country_matches("Boston", parse_places(["Boston, MA"]))  # no country: old rules


def test_the_location_filter_uses_countries_and_still_rejects_others() -> None:
    constraints = HardConstraints(locations=["Germany"])
    assert _location_ok(constraints, ["Munich, DE"], None)[0] is True
    assert _location_ok(constraints, ["Berlin, Germany"], None)[0] is True
    assert _location_ok(constraints, ["Boston, MA"], None)[0] is False
    assert _location_ok(constraints, ["Wilmington, DE"], None)[0] is False
