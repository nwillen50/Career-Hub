# `careerhub.modules.network`

Who the user knows, and where (build step 7, epic CH-28).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `alumni` | Imports a user-provided alumni CSV into `Person` rows | CH-72 | Built |
| `people` | Find and tag people (`how_known`) | CH-72 | Built |
| `paths` | `warm_paths`: who you know at a company, strongest first, with reasons | CH-73 | Built |

LinkedIn connections are imported by `careerhub.profile.linkedin` (CH-56). Alumni come only from a
list you provide; nothing is scraped.

## Commands

```bash
hub people import-alumni alumni.csv     # Name, Company, Group (+ Title, Email, URL)
hub people find acme                    # by name or company
hub people tag 12 former manager        # how you know them
```

A person already known (same URL, email, or name at the same company) is not duplicated; the alumni
tag is added to what you wrote, never replacing it. See
[ADR-0022](../../../../docs/decisions/0022-alumni-and-tags.md).

## Warm paths

`hub paths <company>` lists who you know there, strongest first, with the reasons. Points: your own
words in `how_known` 3, an imported alumni group 2, a LinkedIn connection 1, contact in the last year
1, a recruiter title 1. See [ADR-0023](../../../../docs/decisions/0023-warm-path-rules.md).

## Alumni lists in any layout (CH-100)

`hub alumni import <file> --group "Example Physics"` (alias `hub people import-alumni`) reads CSV
(comma, semicolon, tab, or pipe), TSV, or Excel (`--sheet` to pick a sheet). Title rows above the
header are skipped, and columns are matched by synonym ("Employer", "Current Company",
"Organisation" are all the company). Only a name (or first and last name) and a company are required;
the group comes from a Group column or `--group`. Add `--preview` first: it prints how each column
was mapped (or "ignored") and how many people would be added, tagged, or skipped, with no names and no
changes. `--map company='Current Employer'` overrides one mapping. The synonym table is `SYNONYMS` in
`layouts.py`. See [ADR-0038](../../../../docs/decisions/0038-alumni-layouts.md).

## Same school (CH-101)

People can have schools (`hub people tag 12 --school "Tufts University"`, or a school column in an
alumni import). Someone at a company who went to one of *your* schools (from `profile.md` and your
accepted facts) gets "Same school: ..." and 2 points; an alumni-list member for the same school is not
counted twice. Names match loosely ("Tufts" is "Tufts University"; MIT, UMass... are built in; add
yours with `school_aliases` in `preferences.yaml`). Put `linkedin_school_slug` (for example
`tufts-university`) in `settings.json` and each shortlist card links to LinkedIn's alumni search for
that company. Career Hub only builds the link; it never visits it. See
[ADR-0039](../../../../docs/decisions/0039-same-school.md).
