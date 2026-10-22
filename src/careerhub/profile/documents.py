"""Read the text of a resume or transcript in any common format (CH-90).

`read_document(path)` returns the plain text plus what is known about the file (format, page
count, character count) and a list of **problems**. It never raises for a bad file and never puts
document text into a problem, a log, or an error, because the text is Personal data.

Formats: `.tex`, `.pdf` (pypdfium2), `.docx` (python-docx), `.txt`, `.md`; `.doc`, `.rtf`, `.odt`
through macOS's built-in `textutil` (on other systems they are reported as Mac-only); `.pages` is
reported as "export it as PDF". A PDF with almost no text layer is reported as scanned, because it
needs text recognition (a follow-up that runs on the Mac).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Files bigger than this are refused, so a huge file can't use all memory.
MAX_FILE_BYTES = 20 * 1024 * 1024
#: Fewer characters per page than this means the PDF has no real text layer (a scan).
SCANNED_CHARS_PER_PAGE = 20
TEXTUTIL_FORMATS = {".doc", ".rtf", ".odt"}
SUPPORTED = (".tex", ".pdf", ".docx", ".txt", ".md", *sorted(TEXTUTIL_FORMATS))


@dataclass(frozen=True)
class DocumentText:
    """What was read from one file. `text` is empty when `problems` says why."""

    text: str
    format: str
    pages: int | None
    problems: tuple[str, ...] = ()

    @property
    def chars(self) -> int:
        """The number of characters of text (safe to show; the text itself is not)."""
        return len(self.text)


def read_document(path: Path) -> DocumentText:
    """Return the text of `path`, or an empty text with the reasons in `problems`."""
    suffix = path.suffix.lower()
    kind = suffix.lstrip(".") or "unknown"

    def fail(message: str) -> DocumentText:
        return DocumentText("", kind, None, (message,))

    if suffix == ".pages":
        return fail("Pages files can't be read: export it as PDF from Pages and use that.")
    if suffix not in SUPPORTED:
        return fail(
            f"Unsupported format {suffix or '(none)'!r}. Use one of: {', '.join(SUPPORTED)}."
        )
    try:
        if not path.is_file():
            return fail("The file does not exist.")
        if path.stat().st_size > MAX_FILE_BYTES:
            return fail("The file is larger than 20 MB.")
        if suffix == ".pdf":
            return _read_pdf(path)
        if suffix == ".docx":
            return _read_docx(path)
        if suffix in TEXTUTIL_FORMATS:
            return _read_with_textutil(path, kind)
        raw = path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".tex":
            from careerhub.modules.jobs.tailoring import latex_to_text  # reuses the LaTeX reducer

            raw = latex_to_text(raw)
        return DocumentText(raw.replace("\x00", ""), kind, None)
    except Exception as error:  # a damaged file must not crash; only the error's type is kept
        return fail(f"The file could not be read ({type(error).__name__}).")


def _read_pdf(path: Path) -> DocumentText:
    """Extract the text layer of a PDF, page by page."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = len(pdf)
        parts: list[str] = []
        for index in range(pages):
            page = pdf[index]
            textpage = page.get_textpage()
            parts.append(textpage.get_text_range())
            textpage.close()
            page.close()
    finally:
        pdf.close()
    text = "\n".join(" ".join(line.split()) for part in parts for line in part.splitlines()).strip()
    if pages and len(text.replace("\n", "")) < SCANNED_CHARS_PER_PAGE * pages:
        return DocumentText(
            "",
            "pdf",
            pages,
            ("This PDF looks scanned (no text layer): it needs text recognition.",),
        )
    return DocumentText(text, "pdf", pages)


def _read_docx(path: Path) -> DocumentText:
    """Extract paragraphs and table cells from a Word file."""
    import docx

    document = docx.Document(str(path))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    text = "\n".join(" ".join(line.split()) for line in lines if line.strip())
    return DocumentText(text, "docx", None)


def _read_with_textutil(path: Path, kind: str) -> DocumentText:
    """Convert .doc, .rtf, or .odt with macOS's `textutil`."""
    if sys.platform != "darwin":
        return DocumentText(
            "",
            kind,
            None,
            (f".{kind} files can only be read on a Mac (save it as .docx or PDF instead).",),
        )
    done = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        return DocumentText(
            "", kind, None, (f"textutil could not read the file (exit {done.returncode}).",)
        )
    text = done.stdout.decode("utf-8", errors="replace").replace("\x00", "")
    return DocumentText(
        "\n".join(" ".join(row.split()) for row in text.splitlines() if row.strip()), kind, None
    )
