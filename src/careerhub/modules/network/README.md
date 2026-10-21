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
