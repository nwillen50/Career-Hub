# `careerhub.profile`

Who the user is and what they want (build step 3, epic CH-18). Everything here is Personal data.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `models` | `ProfileDoc`, `Preferences`, `HardConstraints`, `Timeline` | CH-55 | Built |
| `files` | Finds and reads `profile.md` and `preferences.yaml` | CH-55 | Built |
| LinkedIn import | `Connections.csv` into `Person` rows | CH-56 | Planned |
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
