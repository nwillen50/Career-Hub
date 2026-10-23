"""The web app's bundled design assets: the token stylesheet and the local fonts (CH-105).

The app is local-first, so fonts are bundled (all are SIL Open Font License; the licenses are
next to the files) and pages never ask Google Fonts or anything else for them. `tokens_css`
returns the stylesheet to put in a page; `copy_fonts` puts the font files where the page's
`fonts/` URLs point.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path


def tokens_css() -> str:
    """Return the design tokens (colors, fonts, sizes) as CSS text."""
    return (
        resources.files("careerhub.web").joinpath("static/tokens.css").read_text(encoding="utf-8")
    )


def copy_fonts(destination: Path) -> None:
    """Copy the bundled fonts and their licenses into `destination` (a `fonts` folder)."""
    destination.mkdir(parents=True, exist_ok=True)
    source = resources.files("careerhub.web").joinpath("static/fonts")
    for item in source.iterdir():
        if item.name.endswith((".woff2", ".txt")):
            with resources.as_file(item) as path:
                shutil.copyfile(path, destination / item.name)
