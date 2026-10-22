"""Tests for the shared Personal-tier text preparation (CH-107)."""

from __future__ import annotations

import pytest

from careerhub.core.models import Job
from careerhub.modules.jobs import scoring, tailoring
from careerhub.profile.models import ProfileDoc
from careerhub.profile.redact import personal_text, remove_name
from careerhub.profile.rubric import Criterion, Rubric

RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
ABOUT = (
    "Reach me at me@example.com, 617-555-0142 or github.com/someone. "
    "I'm Nicholas Willen, also Nick, handle nwillen50. Annual planning is fun."
)
DOC = ProfileDoc(
    name="Nicholas Willen", aliases=["Nick", "nwillen50"], skills=["Python"], about=ABOUT
)
LEAKS = ["me@example.com", "555-0142", "github.com", "Nicholas", "Willen", "Nick", "nwillen50"]


def make_job() -> Job:
    job = Job(title="Engineer", raw_text="text", structured_fields={})
    job.company = type("C", (), {"name": "Acme"})()  # type: ignore[assignment]
    return job


def test_names_match_whole_words_only() -> None:
    assert remove_name("Annual planning, Ann said", "Ann Lee") == "Annual planning, [name] said"


def test_nicknames_and_handles_are_removed_with_aliases() -> None:
    text = "Nick Willen, Nicholas Willen, nwillen50"
    assert remove_name(text, "Nicholas Willen", ["Nick", "nwillen50"]) == "[name], [name], [name]"


def test_short_or_blank_aliases_are_ignored() -> None:
    assert remove_name("Al went to a mall", "Al Bo", ["", " ", "al"]) == "Al went to a mall"


def test_personal_text_removes_contacts_and_the_name() -> None:
    out = personal_text(ABOUT, DOC.name, DOC.aliases)
    assert not [leak for leak in LEAKS if leak in out]
    assert "Annual planning" in out


def test_scoring_prompt_has_no_contacts_name_or_aliases() -> None:
    prompt = scoring.build_request(make_job(), DOC, RUBRIC).prompt
    assert not [leak for leak in LEAKS if leak in prompt]
    assert "Annual planning" in prompt


def test_tailoring_prompt_has_no_contacts_name_or_aliases() -> None:
    resume = tailoring.resume_for_model(ABOUT, DOC.name, DOC.aliases)
    prompt = tailoring.build_request(make_job(), DOC, resume).prompt
    assert not [leak for leak in LEAKS if leak in prompt]


@pytest.mark.parametrize("name", ["", "  "])
def test_a_blank_name_still_redacts_contacts(name: str) -> None:
    assert personal_text("mail a@b.co", name) == "mail [email]"


# --- Independent review findings (CH-107) ----------------------------------------------------


@pytest.mark.parametrize(
    "phone",
    ["617–555–0142", "617/555/0142", "+44 (0)20 7946 0958", "6 1 7 5 5 5 0 1 4 2", "07911 123456"],
)
def test_phone_numbers_in_other_formats_are_removed(phone: str) -> None:
    from careerhub.profile.redact import personal_text

    assert "[phone]" in personal_text(f"Call {phone} today", "X Y Z")


@pytest.mark.parametrize(
    "link", ["janedoe.xyz", "bit.ly/abc", "jane.app", "jane.page", "jane.tech", "example.co.uk"]
)
def test_more_web_addresses_are_removed(link: str) -> None:
    from careerhub.profile.redact import personal_text

    out = personal_text(f"site: {link}", "Nobody Here")
    assert "[link]" in out and ".uk" not in out.replace("[link]", "")


def test_tech_names_are_not_mistaken_for_links() -> None:
    from careerhub.profile.redact import personal_text

    assert personal_text("Skills: ASP.NET, Node.js, Vue.js, VB.NET", "X Y Z") == (
        "Skills: ASP.NET, Node.js, Vue.js, VB.NET"
    )


@pytest.mark.parametrize(
    "text",
    ["jane at example dot com", "jane (at) example (dot) com", "jane [at] example [dot] com"],
)
def test_spoken_emails_are_removed(text: str) -> None:
    from careerhub.profile.redact import personal_text

    assert "jane" not in personal_text(text, "Nobody Here")


def test_hxxp_links_and_apostrophe_emails_are_removed() -> None:
    from careerhub.profile.redact import personal_text

    assert "evil" not in personal_text("hxxps://evil.example/x", "A B C")
    assert personal_text("o'brien@example.com", "A B C") == "[email]"


def test_accents_invisible_characters_and_letter_spacing_cannot_hide_a_name() -> None:
    import unicodedata

    from careerhub.profile.redact import personal_text

    assert "[name]" in personal_text(unicodedata.normalize("NFD", "Zoë Müller"), "Zoë Müller")
    assert "Willen" not in personal_text("Nicholas Wil­len", "Nicholas Willen")
    assert "Willen" not in personal_text("Nicholas Wil​len", "Nicholas Willen")
    assert "W I L L E N" not in personal_text("W I L L E N", "Nicholas Willen")
    assert "WILLEN" not in personal_text("W.I.L.L.E.N", "Nicholas Willen").replace(".", "")


def test_short_and_hyphenated_name_parts_are_removed() -> None:
    from careerhub.profile.redact import personal_text

    assert personal_text("Wu, Wei", "Wei Wu") == "[name]"
    assert personal_text("Wei Wu led it", "Wei Wu") == "[name] led it"
    assert "Mary" not in personal_text("Mary Jane", "Mary-Jane Watson")
    assert personal_text("I saw Wu there", "Wei Wu") == "I saw Wu there"  # a lone short word stays


def test_a_resume_home_address_is_removed() -> None:
    from careerhub.profile.redact import personal_text

    out = personal_text("Jane\n12 Main Street, Boston, MA 02134\nPython", "Jane Q Sample")
    assert "12 Main Street" not in out and "02134" not in out and "Python" in out


def test_course_lines_are_not_mistaken_for_addresses() -> None:
    from careerhub.profile.redact import personal_text

    line = "PHYS 101 General Physics 4 A- 2023 Dr. Lee"
    assert personal_text(line, "X Y Z") == line


def test_hostile_long_input_runs_in_linear_time() -> None:
    import time

    from careerhub.profile.redact import personal_text

    for blob in ("a." * 40_000, "ab." * 30_000, "a" * 80_000, "1 " * 40_000, "x@" * 30_000):
        start = time.perf_counter()
        personal_text(blob, "Nicholas Willen", ["Nick"])
        assert time.perf_counter() - start < 3.0, blob[:6]


def test_about_is_redacted_before_it_is_cut() -> None:
    doc = ProfileDoc(name="Zed Qux", about="x" * 1490 + " nick@example.com")
    assert "nick@" not in scoring.profile_text(doc)
