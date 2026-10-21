"""Finds the resume and transcript files the user dropped into `inputs/`.

- `inputs/resume/`: the LaTeX source (one main `.tex`, plus any `.cls` or `.sty` it uses)
- `inputs/transcript/`: one PDF

Only the files' presence and the resume's text are read here. Reading the transcript's text needs
a PDF reader and comes later, when something needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from careerhub.core.paths import data_dir


@dataclass(frozen=True)
class ResumeFiles:
    """The LaTeX resume: the main file and the support files next to it."""

    main: Path
    support: list[Path] = field(default_factory=list)

    def text(self) -> str:
        """Return the main file's LaTeX source."""
        return self.main.read_text(encoding="utf-8")


def inputs_dir() -> Path:
    """Return the `inputs` folder in the data folder (it may not exist)."""
    return data_dir() / "inputs"


def find_resume(folder: Path | None = None) -> tuple[ResumeFiles | None, str | None]:
    """Return the resume files, or (None, why not).

    The main file is the one `.tex` file; if there are several, one named `main.tex` or
    `resume.tex` wins, otherwise the choice is ambiguous and reported.
    """
    folder = folder or inputs_dir() / "resume"
    sources = sorted(folder.glob("*.tex")) if folder.is_dir() else []
    if not sources:
        return None, f"No .tex file in {folder.name}/ (inputs/resume)."
    preferred = [path for path in sources if path.stem.lower() in ("main", "resume")]
    if len(sources) > 1 and len(preferred) != 1:
        return None, f"Several .tex files in {folder.name}/; name the main one main.tex."
    main = sources[0] if len(sources) == 1 else preferred[0]
    support = sorted(p for p in folder.iterdir() if p.suffix in (".cls", ".sty"))
    return ResumeFiles(main, support), None


def find_transcript(folder: Path | None = None) -> tuple[Path | None, str | None]:
    """Return the transcript PDF, or (None, why not)."""
    folder = folder or inputs_dir() / "transcript"
    pdfs = sorted(folder.glob("*.pdf")) if folder.is_dir() else []
    if not pdfs:
        return None, f"No PDF in {folder.name}/ (inputs/transcript)."
    if len(pdfs) > 1:
        return None, f"Several PDFs in {folder.name}/; keep just one."
    return pdfs[0], None
