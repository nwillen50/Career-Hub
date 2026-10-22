# `careerhub.profile`

Who the user is and what they want (build step 3, epic CH-18). Everything here is Personal data.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `models` | `ProfileDoc`, `Preferences`, `HardConstraints`, `Timeline` | CH-55 | Built |
| `files` | Finds and reads `profile.md` and `preferences.yaml` | CH-55 | Built |
| `linkedin` | Imports `Connections.csv` into `Person` and `Company` rows | CH-56 | Built |
| `rubric` | `rubric.yaml`: versioned criteria, weights, descriptions | CH-57 | Built |
| `inputs` | Finds the LaTeX resume and the transcript PDF | CH-57 | Built |
| `profile` | `load_profile()` assembles everything; `format_profile()` prints it | CH-57 | Built |

## Files (in the data folder, `profile/`)

`profile.md`: YAML front matter between `---` lines, then free text.

```markdown
---
name: Jane Sample
education:
  - {school: Example University, degree: BS Computer Science, graduation: 2027-05-01}
skills: [Python, SQL]
interests: [machine learning]
---
Anything else Career Hub should know.
```

`preferences.yaml`:

```yaml
hard_constraints:
  locations: ["Boston, MA"]     # empty means anywhere
  remote_ok: true
  min_salary_usd: 90000
  excluded_companies: [Acme]
  excluded_title_words: [senior]
timeline: {earliest_start: 2027-06-01}
weekly_time_budget_hours: 6
```

A missing file gives `None` (the caller reports it). A malformed file raises `ProfileFileError`
naming the wrong *fields*, never their values. Unknown keys are errors, so typos aren't ignored.
See [ADR-0008](../../../docs/decisions/0008-profile-files.md).

## LinkedIn import

`import_connections(session)` reads `inputs/linkedin/Connections.csv` (an unzipped LinkedIn data
export; the notes lines before the header row are skipped). People are matched by profile URL, or
by name and company, so re-importing adds no duplicates. It refreshes only title, company, and
email; your notes and "how I know them" are never touched, and nobody is deleted. A missing file
returns `ImportReport(found=False)`. No scraping: the export is the supported path (PRD).

## `hub profile show`

Run `uv run hub profile show`. It prints the parsed profile, the rubric, which resume and
transcript files were found, how many LinkedIn people are imported, and a "Missing" list. It never
prints resume or transcript text. Missing inputs don't stop it; an invalid file does (exit code 1).
`rubric.yaml` looks like:

```yaml
version: 1
criteria:
  - {name: field, weight: 3, description: The work matches the fields I want.}
```

See [ADR-0009](../../../docs/decisions/0009-profile-assembly.md).

## Preferences added by CH-113

`preferences.yaml` also takes `also_qualified_fields: [...]` (fields you count as your own),
`internship_rank_penalty: 1` (0 to 10; ranks internships lower without changing scores), and
`work_authorization: us_citizen | work_authorized | needs_sponsorship` (so jobs that require US
citizenship can be checked; unset, they are only marked uncertain). `eligibility.py` reads your
highest degree and expected graduation date from `profile.md` and your accepted facts.

## Reading documents (`documents.py`)

`read_document(path)` returns a document's text, format, page count, and problems. It reads `.tex`,
`.pdf`, `.docx`, `.txt`, and `.md` anywhere, and `.doc`, `.rtf`, `.odt` on a Mac (through
`textutil`). `.pages` and scanned PDFs come back with a problem that says what to do. It never
raises on a bad file and never quotes the document in a problem. `inputs/resume/` and `inputs/transcript/`
each take one file of any supported type. See [ADR-0030](../../../docs/decisions/0030-document-reader.md).

## Text sent to models (`redact.py`)

Anything of the user's own that goes into a Personal-tier prompt (profile text, resume, and later
profile extraction) passes through `personal_text`: emails, links, and phone numbers become
`[email]`, `[link]`, `[phone]`, and the name becomes `[name]`. Names match whole words only. List
nicknames and handles in `profile.md` front matter under `aliases:` (for example `["Nick",
"nwillen50"]`) and they are removed too.

`redact.transcript_text` is for transcripts: it also removes student ID numbers, birth dates,
SSN-shaped numbers, and street addresses before contacts and the name. `hub profile show` reports
only each document's format, page count, and character count, plus any reader problems.

## Understanding the documents (`extract.py`)

The nightly run (when a `profile_extract` route exists) reads the current resume and transcript with
a Personal-tier model after redaction, and saves what it found as **pending** facts, keyed by the
file's hash so an unchanged file is never read twice. Nothing counts until the user accepts it
(review commands: CH-110). See [ADR-0031](../../../docs/decisions/0031-profile-extraction.md).

### Reviewing facts (`facts.py`)

`hub profile facts` lists what was read; `hub profile facts accept <n|all>` or `reject <n>` decides.
Accepted facts (newest per document) are added to the profile text for scoring and tailoring, after
redaction; what you typed in `profile.md` wins. See
[ADR-0032](../../../docs/decisions/0032-accepting-extracted-facts.md).
