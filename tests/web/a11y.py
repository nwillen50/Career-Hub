"""A small accessibility checker for server-rendered pages, using only the standard library.

It checks what a person using a screen reader or only a keyboard would notice first: the page
language, one `h1`, landmarks, a skip link that comes first, labelled form fields, image text, and
heading levels that never skip. Colour contrast is checked separately from the design tokens.
"""

from __future__ import annotations

from html.parser import HTMLParser


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lang: str | None = None
        self.tags: list[str] = []
        self.headings: list[int] = []
        self.labels: set[str] = set()
        self.fields: list[tuple[str | None, str | None, bool]] = []  # id, aria-label, hidden
        self.images_without_alt = 0
        self.first_link: str | None = None
        self.inline_style = False
        self.inline_script = False
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        self.tags.append(tag)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append(int(tag[1]))
        elif tag == "label" and a.get("for"):
            self.labels.add(a["for"] or "")
        elif tag in {"input", "select", "textarea"}:
            self.fields.append((a.get("id"), a.get("aria-label"), a.get("type") == "hidden"))
        elif tag == "img" and a.get("alt") is None:
            self.images_without_alt += 1
        elif tag == "a" and self.first_link is None:
            self.first_link = a.get("href")
        elif tag == "script" and not a.get("src"):
            self.inline_script = True
        if "style" in a:
            self.inline_style = True


def check_page(html: str, nav: bool = True) -> list[str]:
    """Return the accessibility problems found in `html` (empty means it passed).

    `nav=False` is for the few pages with no tabs (the "open with the link" page).
    """
    parsed = _Collect()
    parsed.feed(html)
    problems = []
    if not parsed.lang:
        problems.append("the <html> element has no lang")
    if parsed.headings.count(1) != 1:
        problems.append(f"expected one h1, found {parsed.headings.count(1)}")
    for earlier, later in zip(parsed.headings, parsed.headings[1:], strict=False):
        if later > earlier + 1:
            problems.append(f"heading level jumps from h{earlier} to h{later}")
    for landmark in ("main", "header", *(("nav",) if nav else ())):
        if landmark not in parsed.tags:
            problems.append(f"no <{landmark}> landmark")
    if parsed.first_link != "#main":
        problems.append("the first link is not a skip link to #main")
    for field_id, aria, hidden in parsed.fields:
        if not hidden and not aria and field_id not in parsed.labels:
            problems.append(f"form field {field_id!r} has no label")
    if parsed.images_without_alt:
        problems.append("an image has no alt text")
    if parsed.inline_style or parsed.inline_script:
        problems.append("inline style or script (the Content-Security-Policy would block it)")
    return problems


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio of two `#rrggbb` colours (1 to 21)."""
    first, second = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (first + 0.05) / (second + 0.05)
