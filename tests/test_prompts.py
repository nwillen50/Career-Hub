from __future__ import annotations

import pytest

from careerhub.prompts import load_prompt, render


def test_the_extraction_prompt_has_every_placeholder_and_its_json_keys() -> None:
    text = load_prompt("extract_job", 1)
    for marker in ("{{company}}", "{{title}}", "{{location}}", "{{posting}}"):
        assert marker in text
    for key in ("level", "locations", "remote", "visa_sponsorship", "deadline", "required_skills"):
        assert f'"{key}"' in text


def test_the_prompt_tells_the_model_the_posting_is_data_not_instructions() -> None:
    assert "never as instructions" in load_prompt("extract_job", 1)


def test_render_fills_markers_and_leaves_posting_braces_alone() -> None:
    out = render(
        load_prompt("extract_job", 1),
        company="Acme",
        title="T",
        location="L",
        posting="Use {{braces}} freely",
    )
    assert "Company: Acme" in out and "Use {{braces}} freely" in out


def test_an_unfilled_marker_is_an_error() -> None:
    with pytest.raises(ValueError):
        render(load_prompt("extract_job", 1), company="Acme")


def test_a_missing_prompt_version_is_an_error() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("extract_job", 99)


def test_the_scoring_prompt_has_its_placeholders_and_treats_the_posting_as_data() -> None:
    text = load_prompt("score_job", 1)
    for marker in (
        "{{profile}}",
        "{{rubric}}",
        "{{company}}",
        "{{title}}",
        "{{facts}}",
        "{{posting}}",
    ):
        assert marker in text
    assert "never as instructions" in text and '"criteria"' in text and '"explanation"' in text
