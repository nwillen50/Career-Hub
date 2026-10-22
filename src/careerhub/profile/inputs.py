"""Finds the resume and transcript files the user dropped into `inputs/`.

- `inputs/resume/`: one resume in any supported format (see `documents.py`). For LaTeX, the main
  `.tex` plus any `.cls` or `.sty` it uses; with several `.tex` files, name the main one `main.tex`.
- `inputs/transcript/`: one transcript in any supported format.

Reading the text goes through `read_document`; nothing here prints or logs document text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from careerhub.core.paths import data_dir
from careerhub.profile.documents import SUPPORTED, DocumentText, read_document


@dataclass(frozen=True)
class ResumeFiles:
    """The resume: the main file and (for LaTeX) the support files next to it."""

    main: Path
    support: list[Path] = field(default_factory=list)

    def read(self) -> DocumentText:
        """Read the main file (text plus format, page count, and problems)."""
        return read_document(self.main)

    def text(self) -> str:
        """Return the main file's plain text (empty if it couldn't be read)."""
        return self.read().text


def inputs_dir() -> Path:
    """Return the `inputs` folder in the data folder (it may not exist)."""
    return data_dir() / "inputs"


def _candidates(folder: Path) -> list[Path]:
    """Return the readable documents in `folder`, sorted (support files like .cls are not)."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED)


def find_resume(folder: Path | None = None) -> tuple[ResumeFiles | None, str | None]:
    """Return the resume files, or (None, why not).

    With one supported file, that is the resume. With several, one named `main` or `resume`
    (any extension) wins; otherwise the choice is ambiguous and reported.
    """
    folder = folder or inputs_dir() / "resume"
    files = _candidates(folder)
    if not files:
        return (
            None,
            f"No resume file in {folder.name}/ (inputs/resume). Supported: {', '.join(SUPPORTED)}.",
        )
    preferred = [p for p in files if p.stem.lower() in ("main", "resume")]
    if len(files) > 1 and len(preferred) != 1:
        return (
            None,
            f"Several files in {folder.name}/; keep one, or name it main.tex or resume.<ext>.",
        )
    main = files[0] if len(files) == 1 else preferred[0]
    support = sorted(p for p in folder.iterdir() if p.suffix in (".cls", ".sty"))
    return ResumeFiles(main, support), None


def find_transcript(folder: Path | None = None) -> tuple[Path | None, str | None]:
    """Return the transcript file (any supported format), or (None, why not)."""
    folder = folder or inputs_dir() / "transcript"
    files = _candidates(folder)
    if not files:
        return None, f"No transcript file in {folder.name}/ (inputs/transcript)."
    if len(files) > 1:
        return None, f"Several files in {folder.name}/; keep just one."
    return files[0], None
