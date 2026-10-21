# `careerhub.profile`

Who the user is and what they want (build step 3, epic CH-18). Everything here is Personal data.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `models` | `ProfileDoc`, `Preferences`, `HardConstraints`, `Timeline` | CH-55 | Built |
| `files` | Finds and reads `profile.md` and `preferences.yaml` | CH-55 | Built |
| `linkedin` | Imports `Connections.csv` into `Person` and `Company` rows | CH-56 | Built |
| resume, transcript, rubric, `hub profile show` | | CH-57 | Planned |

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
