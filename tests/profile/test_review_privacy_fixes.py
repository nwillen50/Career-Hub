"""Regression tests for the Oct 23 privacy review (CH-149, CH-150, CH-151)."""

from __future__ import annotations

import pytest

from careerhub.llm.providers import LocalProvider
from careerhub.profile.redact import personal_text, transcript_text


@pytest.mark.parametrize(
    "line",
    [
        "Date of Birth: 03-Mar-2003",
        "DOB: 2003/03/03",
        "Date of Birth: 3rd March 2003",
        "DOB: March 3rd, 2003",
        "Date of birth\n03/03/2003",
        "Birth date: 3.3.2003",
    ],
)
def test_birth_dates_in_many_layouts_are_removed(line: str) -> None:
    """CH-149: no digit of the birth date reaches a model."""
    cleaned = transcript_text(f"Transcript\n{line}\nCS101 A", "Li Wu")
    assert "2003" not in cleaned
    assert "CS101 A" in cleaned


@pytest.mark.parametrize("line", ["Student Number: 123456", "Banner: 900123", "Student No 55123"])
def test_short_labelled_student_numbers_are_removed(line: str) -> None:
    cleaned = transcript_text(line, "Li Wu")
    assert not any(ch.isdigit() for ch in cleaned)


def test_course_numbers_are_kept() -> None:
    assert "CS 101" in transcript_text("CS 101 Intro, grade A", "Li Wu")


@pytest.mark.parametrize("text", ["Wu, Li", "Li Wu", "WU,  LI", "Student: Wu, Li (CS)"])
def test_names_made_of_short_parts_go_in_either_order(text: str) -> None:
    """CH-150: 'Last, First' headers are how transcripts print names."""
    cleaned = personal_text(text, "Li Wu")
    assert "Wu" not in cleaned and "Li" not in cleaned


def test_local_provider_ignores_proxy_environment() -> None:
    """CH-151: a prompt for a local model must never be routed through HTTP_PROXY."""
    assert LocalProvider("l", "m")._client.trust_env is False
