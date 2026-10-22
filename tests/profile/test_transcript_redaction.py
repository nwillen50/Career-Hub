"""Transcript redaction and the any-format inputs folders (CH-109). Samples are made up."""

from __future__ import annotations

from pathlib import Path

import pytest

from careerhub.profile.documents import latex_to_text
from careerhub.profile.inputs import find_resume, find_transcript
from careerhub.profile.redact import redact_identity_numbers, transcript_text

TRANSCRIPT = """\
Example University - Official Transcript
Name: Jane Q Sample    Student ID: A12345678    Date of Birth: 04/12/2003
Home address: 123 Maple Street, Apt 4B, Springfield, IL 62704
SSN 123-45-6789, backup 987 65 4321, DOB: March 3, 2003
CS 101 Intro to Programming A 4.0  Fall 2023
MATH 220 Linear Algebra B+ 3.3  Spring 2024
Cumulative GPA 3.65  Email jane.sample@example.edu  Phone (617) 555-0142
"""


def test_identity_numbers_birth_dates_and_addresses_are_removed() -> None:
    out = redact_identity_numbers(TRANSCRIPT)
    for gone in (
        "A12345678",
        "04/12/2003",
        "March 3, 2003",
        "123-45-6789",
        "987 65 4321",
        "123 Maple Street",
        "IL 62704",
    ):
        assert gone not in out, gone


def test_courses_grades_terms_and_gpa_survive() -> None:
    out = redact_identity_numbers(TRANSCRIPT)
    for kept in ("CS 101 Intro to Programming A 4.0  Fall 2023", "MATH 220", "B+ 3.3", "GPA 3.65"):
        assert kept in out, kept


def test_transcript_text_also_removes_contacts_and_the_name() -> None:
    out = transcript_text(TRANSCRIPT, "Jane Q Sample", ["Janie"])
    assert "Jane" not in out and "jane.sample@example.edu" not in out and "555-0142" not in out
    assert "Cumulative GPA 3.65" in out


@pytest.mark.parametrize(
    "text",
    [
        "Student ID 20231234",
        "ID# 99887766",
        "sid: 4455667",
        "Social Security Number: 111-22-3333",
        "Born on file: 1234567890123",
        "Birthdate 2003-04-12",
        "Ref 1234567 end",
    ],
)
def test_other_shapes_of_identifiers_are_removed(text: str) -> None:
    out = redact_identity_numbers(text)
    assert not any(ch.isdigit() for ch in out.replace("[id]", "")), out


def test_hostile_text_does_not_hang_or_crash() -> None:
    hostile = ("1 " * 20000) + ("a" * 100000) + "12 " * 5000
    assert isinstance(redact_identity_numbers(hostile), str)
    assert redact_identity_numbers("") == ""


def test_the_resume_folder_accepts_any_supported_type(tmp_path: Path) -> None:
    (tmp_path / "My Resume.docx").write_bytes(b"x")
    resume, why = find_resume(tmp_path)
    assert why is None and resume is not None and resume.main.suffix == ".docx"


def test_several_resumes_need_a_main_one_in_any_format(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.docx").write_bytes(b"x")
    assert find_resume(tmp_path)[0] is None
    (tmp_path / "resume.docx").write_bytes(b"x")
    resume, _ = find_resume(tmp_path)
    assert resume is not None and resume.main.name == "resume.docx"


def test_unsupported_files_in_the_folder_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "photo.png").write_bytes(b"x")
    assert "No resume file" in (find_resume(tmp_path)[1] or "")
    (tmp_path / "t.txt").write_text("CS101 A")
    assert find_transcript(tmp_path)[0] == tmp_path / "t.txt"


def test_a_text_resume_is_read_without_latex_mangling(tmp_path: Path) -> None:
    (tmp_path / "resume.txt").write_text("Saved 50% of costs {fast}", encoding="utf-8")
    resume, _ = find_resume(tmp_path)
    assert resume is not None and resume.text() == "Saved 50% of costs {fast}"
    assert latex_to_text("Saved 50% of costs") == "Saved 50"  # LaTeX treats % as a comment


@pytest.mark.parametrize(
    "text",
    [
        "Date of Birth: 04 March 2001",
        "D.O.B. 03/04/2001",
        "Born: 03/04/2001",
        "Birth date (MM/DD/YYYY): 03/04/2001",
        "DOB 2001-03-04",
    ],
)
def test_more_birth_date_forms_are_removed(text: str) -> None:
    assert "2001" not in redact_identity_numbers(text)


@pytest.mark.parametrize(
    "text",
    [
        "University ID: A12345678",
        "Student Identification Number: U12345678",
        "Campus ID U1234567",
        "UID: 123456",
        "SSN on file 123456789.",
        "Ref A12345678 end",
    ],
)
def test_more_id_forms_are_removed(text: str) -> None:
    out = redact_identity_numbers(text)
    assert not any(ch.isdigit() for ch in out), out


def test_ssn_with_unicode_dashes_is_removed() -> None:
    assert "6789" not in redact_identity_numbers("123‑45–6789")


def test_a_course_line_keeps_its_grade_and_credits() -> None:
    line = "PHYS 101 General Physics 4 A- 2023 Dr. Lee"
    assert transcript_text(line, "X Y Z") == line
    assert "3.65" in transcript_text("Cumulative GPA 3.65", "X Y Z")
