# Architecture decision records

Each file here records one decision: the context, what we chose, what we rejected, and the
consequences. They are numbered in order and never deleted; a reversed decision gets a new record
that supersedes the old one.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-encrypted-storage.md) | Encrypted storage with SQLCipher, SQLAlchemy, and Alembic | Accepted |
| [0002](0002-data-models-and-migrations.md) | Data models in SQLAlchemy, UTC times, tables added by the step that needs them | Accepted |
| [0003](0003-router-contract.md) | Router contract: ordered privacy tiers, one choke point, no fallback on refusal | Accepted |
| [0004](0004-core-tables-match-prd.md) | Core tables follow the PRD's data model; JSON for list fields; proposals end `rejected` | Accepted |
| [0005](0005-response-cache.md) | Response cache keyed by provider, model, and prompt hash; checked after privacy | Accepted |
| [0006](0006-cost-log-and-budget.md) | Cost log per call, monthly budget setting, essential calls exempt | Accepted |
| [0007](0007-real-providers.md) | Real providers as thin httpx adapters; local must be localhost | Accepted |
| [0008](0008-profile-files.md) | Profile files: Markdown front matter and YAML, strict models, errors without values | Accepted |
| [0009](0009-profile-assembly.md) | Profile assembly: missing inputs reported, invalid ones fatal; `hub profile show` | Accepted |
| [0010](0010-broad-job-source.md) | Broad job source: Adzuna, pending Nicholas checking its terms | Proposed |
| [0011](0011-job-table-and-dedupe.md) | Job table, identity by source and external id, cross-source dedupe key | Accepted |
| [0012](0012-source-approval.md) | Sources start proposed; only a user action approves; fetching requires approval | Accepted |
| [0013](0013-greenhouse-adapter.md) | Greenhouse adapter: public feed; a failed fetch changes nothing | Accepted |
| [0014](0014-job-extraction.md) | Job extraction: versioned prompt, strict schema, public tier, skip if already done | Accepted |
| [0015](0015-router-from-settings.md) | Router built from settings: providers, task routes, Keychain keys, budget | Accepted |
| [0016](0016-hard-filters-and-assessment.md) | Assessment table; hard filters reject only on known facts, with reasons | Accepted |
| [0017](0017-rubric-scoring.md) | Rubric scoring: model rates criteria, code computes the score; Personal tier | Accepted |
| [0018](0018-ranking-eval.md) | Ranking eval runs the full pipeline in a scratch database; PRD pass rule | Accepted |
| [0019](0019-shortlist-page.md) | Shortlist page: static, escaped, http(s) links only, error and empty states | Accepted |
| [0020](0020-nightly-run.md) | Nightly run: keep going on failures, fail only when nothing worked; 2x ceiling on essential spend | Accepted |
| [0021](0021-launchd-schedule.md) | launchd: write the job file, user starts it; runs the venv's Python | Accepted |
| [0022](0022-alumni-and-tags.md) | Alumni CSV import and tagging; user's words never overwritten | Accepted |
| [0023](0023-warm-path-rules.md) | Warm-path ranking: transparent points, user's words first | Accepted |
| [0024](0024-tailoring-notes.md) | Tailoring notes: advice only, Personal tier, contact details removed | Accepted |
| [0025](0025-review-fixes.md) | Fixes from an independent review: cache only valid answers, stale scores, duplicates, empty boards | Accepted |
| [0026](0026-free-busy-from-calendar-files.md) | Free/busy from calendar files first; intervals only, no event text | Accepted |
| [0027](0027-weekly-plan.md) | Weekly plan: suggestions only, urgent first, budget and daily share limits | Accepted |
| [0028](0028-review-fixes-2.md) | Second review: stronger redaction, unusable answers never cached, copies, plan cap, bad events | Accepted |
| [0029](0029-model-catalog.md) | Model catalog: presets switched on by a key, default routes that respect privacy | Accepted |
| [0030](0030-document-reader.md) | Document reader: pypdfium2 and python-docx, textutil on the Mac, scans reported | Accepted |
| [0031](0031-profile-extraction.md) | Profile extraction: redacted, validated, saved as pending facts keyed by file hash | Accepted |
| [0032](0032-accepting-extracted-facts.md) | Extracted facts count only when accepted; profile.md wins; newest accepted set per document | Accepted |
| [0033](0033-company-names.md) | Company key ignores legal suffixes and web endings; one create/find path; migration merges duplicates | Accepted |
| [0034](0034-add-a-job-by-link.md) | Add a job by link: public ATS APIs only, LinkedIn/Handshake never requested, manual jobs never auto-closed | Accepted |
| [0035](0035-closed-vs-glitch.md) | Empty board: spot-check three jobs, else a three-night backstop; closures noted in the summary | Accepted |
| [0036](0036-posting-age.md) | Posting age: source date or first seen; footnote; slight ordering penalty after 21 days | Accepted |
| [0037](0037-profile-readiness.md) | Scoring starts when the profile is ready; `hub profile ready` switches it on | Accepted |
| [0038](0038-alumni-layouts.md) | Alumni lists in any layout: sniffed delimiter, Excel, synonym columns, preview | Accepted |
| [0039](0039-same-school.md) | Same-school warm paths: schools on people, school_key, an alumni search link that is built but never fetched | Accepted |
| [0040](0040-apple-calendar.md) | Apple Calendar via EventKit: five facts only, lazy import, fake store in tests | Accepted |
| [0041](0041-company-aliases-and-suggestions.md) | Company aliases are the user's, set in preferences; near-duplicate names are suggestions, never automatic merges | Accepted |
| [0042](0042-merged-companies-stay-merged.md) | A merged company's old name keeps finding the company (`merged_keys`) | Accepted |
| [0043](0043-interface-stack.md) | Interface stack: FastAPI, Jinja2, bundled HTMX 2.0.11, strict CSP, and how pages are tested | Accepted |
| [0044](0044-skills-are-a-light-signal.md) | Skills are a light, partial signal; pay is scored; one skill is enough to start | Accepted |
| [0045](0045-splitting-merged-companies.md) | A company merge is logged so it can be undone exactly | Accepted |
| [0046](0046-school-table-and-related-schools.md) | A reviewed school table; branch-campus and same-system ties; ambiguous abbreviations | Accepted |
| [0047](0047-keep-yaml-comments.md) | Editing the user's YAML keeps comments and layout (ruamel.yaml, writing only) | Accepted |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD
- Ticket: CH-n
- Deciders: who

## Context
What problem, and what constraints.

## Decision
What we chose, stated plainly.

## Evidence
What we tested, and the results.

## Alternatives considered
What else we looked at, and why not.

## Consequences
What this makes easier, harder, or requires next.
```
