# Evals

Hand-checked data that measures whether a model task is accurate. An eval gates every change to
its prompt or model (PRD, "Evaluation and quality").

## `extraction/`

- `sample.yaml`: three made-up postings in the file format, used by the tests.
- `real.yaml` (to come, CH-65): about 20 real, public postings that Nicholas has hand-checked.

Format (see the comment at the top of `sample.yaml`): a list of `cases`, each with `id`, `company`,
`title`, `location`, the posting `text`, and `expected` answers for `level`, `locations`,
`visa_sponsorship`, and `deadline`.

Optional expected values (CH-98): `degree_required`, `equivalent_experience_ok`, and
`work_authorization` are scored only for cases that state them.

Scoring (`careerhub.modules.jobs.evals`): `level`, `visa_sponsorship`, and `deadline` must match
exactly (no deadline expected means none found); `locations` must match as a set, ignoring case,
spacing, and order. A case the model can't answer validly counts as wrong on every field.

```python
from careerhub.modules.jobs.evals import load_cases, run_extraction_eval

report = run_extraction_eval(
    router, load_cases(Path("evals/extraction/real.yaml")), ["cheap", "strong"]
)
print(report.summary())
```

A `hub eval extract` command arrives with CH-64, which builds the router from settings.

## `ranking/`

- `sample.yaml`: three made-up labeled postings. `real.yaml` (CH-7, Nicholas) will hold about 30.
- Each case: `id`, `company`, `title`, `location`, `text`, `label` (`great`, `okay`, or `no`), and a
  one-line `reason`. An unquoted `no` is fine.
- Passing means no `great` posting is filtered out and at most one `no` is in the top 10. Also
  reported: how often label pairs are in the right order, and the mean score per label.

## `profile/`

`sample.yaml`: made-up resumes and transcripts with the facts a good reading must find (GPA,
skills, schools, organizations, course codes). `careerhub.profile.evals.run_profile_eval` reports
the share of expected items found per field and lists the misses.

### Your own labeled postings and "Check the effect" (CH-172)

Put your labeled postings (the `ranking/sample.yaml` format) in `evals/ranking.yaml` inside your
data folder. After you accept a rubric change in the Conversation panel, "Check the effect" runs
the ranking eval with the old and the new rubric on them and shows the filter eval, the top-10
eval, and the ordering share side by side. It says roughly how many model calls first, and only
runs when you press the button. About 30 labeled postings make the numbers mean something.
