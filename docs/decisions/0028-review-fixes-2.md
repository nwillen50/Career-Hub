# 0028. Fixes from the second independent review

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-79 (bug)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Decisions

1. **Contact redaction covers realistic resumes.** LaTeX escapes (`\_`, `\&`, ...) are removed first;
   `mailto:` and `tel:` prefixes are dropped; web addresses without `https://` (linkedin.com/in/...,
   github.com/..., name.dev) are removed; phone numbers must look like phone numbers (3-3-4 digits,
   optional country code, or an international `+CC` form), so GPAs and year ranges are kept.
2. **The user's name and any contact details in the profile text are removed too** before a model
   sees them (`[name]`), correcting ADR-0024's claim that only the profile block omitted the name.
3. **A schema-valid but unusable answer is retried and never cached.** `complete_validated_detailed`
   takes a `check` (scoring passes "scores exactly the rubric's criteria"); failing it counts as a
   validation failure, so it is retried, escalated, and kept out of the cache.
4. **One copy of a job stands in when its original closes**: among copies linked to a closed
   original, only the lowest-id open copy is shown.
5. **Tailoring notes are redone when the profile changes** (the staleness hash now includes the
   profile text and company).
6. **The daily cap in the weekly plan is never smaller than the longest task**, so a small budget
   doesn't make every task unplaceable.
7. **A calendar event with no usable start is skipped**, so one bad event doesn't break `hub plan`.

## Evidence

`tests/test_review_fixes_2.py`; every test failed before its fix.

## Consequences

- Regex redaction is still best-effort; names inside free text are caught only if they match the
  profile name, and unusual formats could slip through.
- Bare-domain removal could blank a rare tech name ending in `.io` or `.ai`; acceptable.
