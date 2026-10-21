# Evals

Hand-checked data that measures whether a model task is accurate. An eval gates every change to
its prompt or model (PRD, "Evaluation and quality").

## `extraction/`

- `sample.yaml`: three made-up postings in the file format, used by the tests.
- `real.yaml` (to come, CH-65): about 20 real, public postings that Nicholas has hand-checked.

Format (see the comment at the top of `sample.yaml`): a list of `cases`, each with `id`, `company`,
`title`, `location`, the posting `text`, and `expected` answers for `level`, `locations`,
`visa_sponsorship`, and `deadline`.

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
