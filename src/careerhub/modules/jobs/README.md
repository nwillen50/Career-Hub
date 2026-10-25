# `careerhub.modules.jobs`

Discover postings and keep one row per job (build step 4, epic CH-21).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `store` | `NormalizedJob`, `store_jobs`: upsert, close missing, link duplicates | CH-59 | Built |
| `sources` | Propose, approve, reject, disable sources; `require_approved` | CH-61 | Built |
| `extract` | `JobFields` and `extract_and_save`: posting text into structured fields | CH-62 | Built |
| `careers_page` | `track_company`: follow a company and find its board from its own careers page | CH-188 | Built (the web button is CH-198) |
| `rank_evals` | `run_ranking_eval`: labeled postings through the whole pipeline | CH-68 | Built |
| `tailoring` | `tailor_job`: what to lead with, gaps, resume edits (notes only) | CH-75 | Built |
| `rank_pairs` | `run_pair_eval`: two postings that differ in one way (an unlisted skill, pay) and how their scores compare | CH-175 | Built |
| `scoring` | `score_job`, `ranked_jobs`: rubric scores with explanations | CH-67 | Built |
| `filters` | Hard filters (companies, title words, locations, salary) with reasons | CH-66 | Built |
| `evals` | `run_extraction_eval`: per-field accuracy against hand-checked postings | CH-63 | Built |
| `greenhouse` | Fetch a company's public board; `hub ingest greenhouse` | CH-60 | Built |
| `lever` | Same for Lever (`api.lever.co`); `hub ingest lever` | CH-93 | Built |
| `ashby` | Same for Ashby (`api.ashbyhq.com`), with stated pay added to the text; `hub ingest ashby` | CH-94 | Built |
| `muse` | The Muse search (partial listing; shortlist says "via The Muse"); `themuse` sources | CH-95 | Built |
| `manual` | `hub add-job <url>`: a link from an ATS, or any site with pasted text | CH-96 | Built |
| `find_boards` | `hub targets find-boards`: guess each followed company's board, verify by name, propose a source (on demand only) | CH-112 | Built |
| `ingest`, `adapters` | The shared fetch-and-store routine and the adapter registry | CH-93 | Built |

An adapter turns its feed into `NormalizedJob`s and calls `store_jobs(session, source, jobs)` with
the source's **complete** listing. A rerun adds no duplicates; postings that disappear are marked
`closed`, not deleted; the same job at two sources is linked with `duplicate_of_id`.
See [ADR-0011](../../../../docs/decisions/0011-job-table-and-dedupe.md).

## Sources and approval

Sources start `proposed`. Only `approve_source` (via `hub sources approve <id>`) makes one usable,
and fetching code must call `require_approved` first. Commands: `hub sources list`,
`hub sources add <kind> <name> <url>`, `hub sources approve|reject|disable <id>`.
See [ADR-0012](../../../../docs/decisions/0012-source-approval.md).

## Lever

`hub sources add lever Acme https://jobs.lever.co/acme`, then `hub sources approve <id>`, then
`hub ingest lever --company acme`. Companies on Lever's European site add `"region": "eu"` to the
source's config. Same rules as Greenhouse: a failed fetch changes nothing, and an empty listing for
a board that had jobs is treated as a glitch. The nightly run fetches every approved source whose
kind is in `adapters.ADAPTERS`. The response shape was built from Lever's public documentation
(not checked against a live board from the build environment; see the PR).

## Ashby

`hub sources add ashby Acme https://jobs.ashbyhq.com/acme`, approve, then
`hub ingest ashby --company acme`. When a posting states a salary, a "Compensation" line is added to
its text so extraction and the salary filter see it. Unlisted postings are skipped. Shape built from
Ashby's public documentation (not checked against a live board from the build environment).

## The Muse

`hub sources add themuse "The Muse" https://www.themuse.com --level "Entry Level" --category
"Software Engineering" --pages 3` (also `--location`), then approve it. It is a **partial** source: jobs missing from tonight's search stay open and
close only after 14 days unseen. Every job keeps its Muse address and the page credits "via The
Muse". The nightly run searches it with the other sources. See ADR-0010 and ADR-0011's amendment.

## Add a job by link

`hub add-job <url>` works for any link. A Greenhouse, Lever, or Ashby posting address is fetched
through its public API. For LinkedIn, Handshake, or any other site the page is **never requested**:
give `--company` and `--title` (or let them be read from the link's own text), and if the job is
already known or on a company board you have an approved source for, that copy is used; otherwise
paste the description with `--text-file <file>` or on stdin. Manual jobs are never closed by Career
Hub. See [ADR-0034](../../../../docs/decisions/0034-add-a-job-by-link.md).

## Target companies

`hub targets import docs/seeds/engine-resident-companies.yaml` adds The Engine's 137 resident
companies (public list) to `targets.yaml` in your data folder, all **disabled**; importing again
changes nothing and never touches a company you already have. `hub targets list` shows them;
`hub targets enable <name>` / `disable <name>` follow or stop following one;
`hub targets enable-matching` follows the ones whose sectors match your interests in `profile.md`
(whole words: "robotics" matches "AUTONOMOUS SYSTEMS / ROBOTICS"). Finding each company's job board
is the next ticket.

## When a board lists no jobs

If a board that had open jobs suddenly lists none, Career Hub asks the system about up to three of
those jobs by address (Greenhouse and Lever). If all are "not found", the company really closed them
and they close here too, with a line in the nightly summary. If any is still there, or the check
fails, it is treated as a glitch and the jobs stay open. Ashby has no such address, so after
`empty_listing_backstop_nights` (default 3) empty nights in a row its jobs close anyway.
See [ADR-0035](../../../../docs/decisions/0035-closed-vs-glitch.md).

## Greenhouse

`hub sources add greenhouse Acme https://boards.greenhouse.io/acme`, then
`hub sources approve <id>`, then `hub ingest greenhouse --company acme`. A failed fetch changes
nothing (jobs are never closed by a bad answer) and marks the source `failing`.
See [ADR-0013](../../../../docs/decisions/0013-greenhouse-adapter.md).

## Extraction

`extract_and_save(session, router, job, routes=["cheap", "strong"])` asks a model (prompt
`prompts/extract_job_v1.md`) for level, locations, remote, visa sponsorship, deadline, and more,
validates the answer, and saves it in `job.structured_fields` with the prompt version. A job already
extracted with this version is skipped. See
[ADR-0014](../../../../docs/decisions/0014-job-extraction.md).

## Extraction evals

`run_extraction_eval(router, load_cases(path), routes)` measures accuracy for level, locations,
visa sponsorship, and deadline and lists every miss. File format and scoring rules:
[`evals/README.md`](../../../../evals/README.md). The real hand-checked set is CH-65 (Nicholas).

## Hard filters

`assess_filters(session, job, preferences.hard_constraints)` saves an `Assessment` with
`filter_passed` and `filter_results` (`reasons` for a rejection, `uncertain` for facts it couldn't
check). A job is rejected only when a known fact breaks a rule; missing information passes. See
[ADR-0016](../../../../docs/decisions/0016-hard-filters-and-assessment.md).

## Scoring

`score_job(session, router, job, profile_doc, rubric, routes)` has the model rate each rubric
criterion (0 to 10) and explain; the code computes the 0-100 fit score and saves it on the job's
`Assessment` with the model, prompt version, and rubric version. Only filtered-in jobs are scored.
`ranked_jobs(session)` lists open, original jobs best first. See
[ADR-0017](../../../../docs/decisions/0017-rubric-scoring.md).

## Ranking evals

`run_ranking_eval(router, load_labeled(path), doc, rubric, constraints, extract_routes,
score_routes)` runs great/okay/no labeled postings through extraction, filters, and scoring in a
scratch in-memory database and reports whether the PRD rule holds (no great filtered out, at most
one "no" in the top 10). See [ADR-0018](../../../../docs/decisions/0018-ranking-eval.md) and
[`evals/README.md`](../../../../evals/README.md).

## Tailoring notes

For each shortlisted job (nightly, if a `tailor` route and `inputs/resume` exist) a model writes
notes: what to lead with, gaps to address, resume edits to consider. Notes only; nothing is edited or
sent. The resume goes to the model as plain text with emails, links, and phone numbers removed, and
only to providers allowed to see Personal data. See
[ADR-0024](../../../../docs/decisions/0024-tailoring-notes.md).

## Looser matching (CH-87)

Excluded title words match the exact word (rejects the job) and also plurals, endings and
abbreviations: "intern" also sees "interns" and "internship", "senior" sees "Sr.", "manager" sees
"Mgr". Those looser hits never filter a job out: they are listed as *uncertain* with the words
involved ("'sr' in the title looks like your excluded word 'senior'"). "Internal" is not "intern".
Places match by city substring as before, and also by built-in metro names (NYC, Manhattan, SF, Bay
Area, DC...) and your own `location_aliases` in `preferences.yaml`, as whole words only.

## Posting age (CH-81)

Each card on the shortlist ends with one muted line, such as "Posted 24 days ago" (past
`stale_after_days`, default 21: "..., older than 3 weeks"). The age is the source's posting date when
its record has one (`age.py` lists the fields), otherwise the day Career Hub first saw the job. An
old posting is ordered slightly lower (`stale_penalty_points`, default 5 of 100) but keeps its score
and is never hidden. See [ADR-0036](../../../../docs/decisions/0036-posting-age.md).

## Scoring v2 (CH-114)

Scoring (`score_job_v3.md`) sees every extracted fact except nice-to-have skills ("unknown" stays
unknown), your preferences (locations, remote, salary, start window), and your degree, expected
graduation, and `also_qualified_fields`. It treats new grad, entry level, associate, and 0 to 2
years as equal, counts adjacent fields, and scores internships like any job. Nice-to-have skills
cannot change a score; they go to the tailoring notes instead. `hub profile show` suggests a
"qualifications" rubric criterion if yours has none (it never edits the file).

## Eligibility filters (CH-113)

Besides the constraints above, three eligibility rules can filter a job, each with its reason:
the **degree level** it requires (above your highest completed or in-progress degree, unless it
accepts equivalent experience, which only makes it "uncertain"), a **graduation window** that
excludes your expected graduation date, and **US citizenship** if you set `work_authorization` in
`preferences.yaml` to something other than `us_citizen`. A clearance is always only "uncertain".
Degree *field* and job *level* (new grad, entry, internship) never filter. `internship_rank_penalty`
(0 to 10, default 0) ranks internships a little lower without changing their score, and the page
says so. `also_qualified_fields` lists fields you count as your own (used by scoring, CH-114).

## Places (CH-120)

`places.py` reads location text ("Munich, DE", "Remote - EU") into city, region, and an ISO
country code, with no model call. Unreadable text keeps its original and an unknown country;
`DE`/`CA`-style codes are read as a US state unless the city is a well-known foreign one. The
location filter now also matches by country, so a preference of "Germany" matches "Berlin,
Germany" and "Munich, DE". Decision (flagged for Nicholas): places are parsed when needed rather
than stored in a new column, so there is no migration and no re-extraction; the parser is cheap
and deterministic. CH-121 (grouping) and CH-122 (work-visa flags) can use `parse_places` directly.

### find-boards safeguards (CH-213)

A one-word guess left after dropping words from a longer name ("Quantum Machines" to `quantum`) is never tried,
a board counts as the company's only if every distinctive word of the name appears in it, and a board already
held by one target is listed as uncertain for any other target instead of being proposed twice.
