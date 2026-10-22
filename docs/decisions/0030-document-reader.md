# 0030. Reading resumes and transcripts in any common format

- Status: Accepted (new dependencies flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-90, for epic CH-18 (step 3)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

The profile code read only a LaTeX resume and only checked that a transcript PDF existed. Other
users will have Word, PDF, and other files, and Career Hub needs the text of both documents.

## Decision

1. **One function**, `read_document(path)`, returns the text, format, page count, and a list of
   problems. It never raises for a bad file and never puts document text in a problem or log.
2. **Libraries**: `pypdfium2` for PDF (Apache-2.0 or BSD-3), `python-docx` for Word (MIT, which
   brings `lxml`, BSD). PyMuPDF is not used: it is AGPL and this repo is MIT.
3. **`.doc`, `.rtf`, `.odt`** use macOS's built-in `textutil`; elsewhere they are reported as
   Mac-only. **`.pages`** is reported as "export it as PDF".
4. **Scanned PDFs** (under 20 characters per page) are reported as needing text recognition. macOS
   Vision text recognition is a follow-up for a Mac session.
5. Files over 20 MB are refused. Wiring into `inputs/`, tailoring, and transcript redaction is CH-109.

## Evidence

`tests/profile/test_documents.py`: generated txt, md, tex, docx, and PDF samples; scanned, broken,
huge, empty, and non-UTF-8 files; Mac-only formats and `textutil` with a stand-in.

## Consequences

CH-109 wired the reader into `inputs/`, tailoring (which now takes plain text; the LaTeX reducer
moved to `profile.documents`), transcript redaction, and `hub profile show`.
