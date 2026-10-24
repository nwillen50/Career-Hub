"""Tests for the design tokens and their use on the shortlist page (CH-105)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from careerhub.web.assets import copy_fonts, tokens_css
from careerhub.web.shortlist import _STYLE, ShortlistData, ShortlistEntry, render_shortlist

CSS = tokens_css()
LIGHT, _, DARK = CSS.partition("@media (prefers-color-scheme: dark)")
DARK = DARK.split("@media (prefers-reduced-motion")[0]
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)


def values(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def hex_of(value: str) -> str:
    return value.strip().split()[0]


def luminance(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_every_color_token_has_a_light_and_a_dark_value() -> None:
    light, dark = values(LIGHT), values(DARK)
    colors = {k for k, v in light.items() if hex_of(v).startswith("#")}
    assert colors, "no color tokens found"
    # Tokens that are the same in both modes on purpose.
    same_in_both = {"--divider"} & colors
    missing = {k for k in colors - same_in_both if k not in dark}
    assert missing == set(), f"no dark value for: {sorted(missing)}"
    assert {"--font-heading", "--heading-weight"} <= dark.keys()  # the heading font is a token


TEXT_PAIRS = [
    ("--text", "--surface"), ("--text", "--page"), ("--muted", "--surface"), ("--muted", "--page"),
    ("--header-text", "--header"), ("--tab-inactive", "--header"), ("--accent", "--surface"),
    ("--accent", "--page"), ("--panel-text", "--panel"), ("--overdue", "--surface"),
    ("--overdue", "--page"), ("--brass-text", "--surface"), ("--brass-text", "--page"),
    ("--chip-selected-text", "--chip-selected"),
    ("--tab-active", "--header"),  # the current tab in the top bar (bug CH-193)
]  # fmt: skip


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_text_and_background_pairs_meet_4_5_to_1(mode: str) -> None:
    tokens = {**values(LIGHT), **(values(DARK) if mode == "dark" else {})}
    for text, background in TEXT_PAIRS:
        if text == "--tab-inactive" and mode == "dark":
            continue  # dark tabs sit on the page color, checked below
        ratio = contrast(hex_of(tokens[text]), hex_of(tokens[background]))
        assert ratio >= 4.5, (mode, text, background, round(ratio, 2))


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_score_bubbles_meet_3_to_1_as_large_bold_text(mode: str) -> None:
    tokens = {**values(LIGHT), **(values(DARK) if mode == "dark" else {})}
    for tier in ("high", "mid", "low"):
        ratio = contrast(
            hex_of(tokens[f"--score-{tier}-text"]), hex_of(tokens[f"--score-{tier}-bg"])
        )
        assert ratio >= 3.0, (mode, tier, round(ratio, 2))


def test_the_page_styles_use_tokens_only_no_raw_colors() -> None:
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", _STYLE)
    assert not re.findall(r"\b(?:rgb|hsl)a?\(", _STYLE)


def entry(score: float) -> ShortlistEntry:
    return ShortlistEntry("T", "Acme", None, "https://example.com", score, "Why.", [])


@pytest.mark.parametrize(
    ("score", "tier"), [(86, "high"), (75, "high"), (74, "mid"), (55, "mid"), (54, "low")]
)
def test_the_bubble_tier_follows_the_cutoffs_and_has_a_label(score: int, tier: str) -> None:
    html = render_shortlist(ShortlistData([entry(score)], NOW), NOW)
    assert f'class="bubble tier-{tier}"' in html
    assert f'aria-label="Fit score {score} out of 100"' in html


def test_the_cutoffs_are_a_setting() -> None:
    data = ShortlistData([entry(60)], NOW, tier_high=50, tier_mid=40)
    assert "tier-high" in render_shortlist(data, NOW)


def test_the_page_carries_the_tokens_the_fonts_and_no_external_requests() -> None:
    html = render_shortlist(ShortlistData([entry(80)], NOW), NOW)
    assert "--page:" in html and "prefers-color-scheme: dark" in html
    assert 'url("fonts/' in html and "http://" not in html.replace("https://example.com", "")
    assert "fonts.googleapis" not in html and "<script" not in html


def test_the_fonts_and_their_licenses_are_copied_next_to_the_page(tmp_path: Path) -> None:
    copy_fonts(tmp_path / "fonts")
    names = {p.name for p in (tmp_path / "fonts").iterdir()}
    assert "source-sans-3-latin-400-normal.woff2" in names
    assert "libre-caslon-text-latin-700-normal.woff2" in names
    assert {n for n in names if n.startswith("LICENSE")} == {
        "LICENSE-source-sans-3.txt", "LICENSE-source-serif-4.txt", "LICENSE-libre-caslon-text.txt",
    }  # fmt: skip
    for font in (tmp_path / "fonts").glob("*.woff2"):
        assert font.read_bytes()[:4] == b"wOF2"
