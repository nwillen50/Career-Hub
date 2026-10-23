"""Grouping the shortlist by place (CH-121)."""

from __future__ import annotations

from datetime import UTC, datetime

from careerhub.web.grouping import REMOTE_GROUP, UNKNOWN_GROUP, group_by_place
from careerhub.web.shortlist import ShortlistData, ShortlistEntry, render_shortlist

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def entry(title: str, location: str | None, score: float = 80) -> ShortlistEntry:
    return ShortlistEntry(title, "Acme", location, "https://x.example/1", score, "Good.", [])


def shape(locations: list[str | None]) -> list[tuple[str, list[tuple[str, list[int]]]]]:
    return [(g.name, [(c.name, c.positions) for c in g.cities]) for g in group_by_place(locations)]


def test_countries_by_best_rank_then_cities_then_remote_then_unknown() -> None:
    locations = [
        "Berlin, Germany",  # 0
        "Boston, MA",  # 1
        "Remote",  # 2
        "Munich, DE",  # 3
        "Building 7, Campus Drive",  # 4
        "Cambridge, MA",  # 5
        "Berlin, Germany",  # 6
        None,  # 7
    ]
    assert shape(locations) == [
        ("Germany", [("Berlin", [0, 6]), ("Munich", [3])]),
        ("United States", [("Boston", [1]), ("Cambridge", [5])]),
        (REMOTE_GROUP, [("", [2])]),
        (UNKNOWN_GROUP, [("", [4, 7])]),
    ]


def test_rank_order_is_kept_inside_a_group_and_empty_groups_are_left_out() -> None:
    groups = group_by_place(["Boston, MA", "Boston, MA", "Boston, MA"])
    assert [g.name for g in groups] == ["United States"] and groups[0].count == 3
    assert groups[0].cities[0].positions == [0, 1, 2]
    assert group_by_place([]) == []


def test_a_job_with_several_places_is_listed_once_under_its_first() -> None:
    groups = group_by_place(["Berlin, Germany; Boston, MA"])
    assert [g.name for g in groups] == ["Germany"] and groups[0].count == 1


def test_a_country_without_a_city_gets_a_plain_heading() -> None:
    assert shape(["Germany"]) == [("Germany", [("City not stated", [0])])]


def page(group_by: str, locations: list[str | None]) -> str:
    entries = [entry(f"Job {i}", loc, 90 - i) for i, loc in enumerate(locations)]
    return render_shortlist(ShortlistData(entries, group_by=group_by), NOW)


def test_the_place_page_has_real_headings_with_counts_and_keeps_rank_numbers() -> None:
    html = page("place", ["Boston, MA", "Berlin, Germany", "Berlin, Germany", "Remote"])
    assert "<h2>United States (1 job)</h2>" in html
    assert "<h2>Germany (2 jobs)</h2>" in html and "<h3>Berlin (2 jobs)</h3>" in html
    assert "<h2>Remote (1 job)</h2>" in html
    assert "<h4>2. " in html and "<h4>1. " in html  # cards sit below the headings; ranks stay
    assert html.index("United States") < html.index("Germany")  # best-ranked job first


def test_the_default_page_is_unchanged_and_the_location_always_shows() -> None:
    html = page("rank", ["Boston, MA", "Berlin, Germany"])
    assert "<h2>1. " in html and "<h2>2. " in html and "United States (" not in html
    grouped = page("place", ["Boston, MA"])
    assert "Boston, MA" in grouped  # the card headline still shows the place


def test_hostile_place_text_is_escaped_in_headings() -> None:
    html = page("place", ["<script>alert(1)</script>, Berlin, Germany"])
    assert "<script>alert" not in html
