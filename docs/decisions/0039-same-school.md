# 0039. Same-school warm paths

- Status: Accepted
- Date: 2026-10-22
- Ticket: CH-101, for epic CH-28 (step 7)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Someone at a company who went to the user's school is a good person to ask even if they are on no
alumni list. LinkedIn's connections export has no schools, so Career Hub knows a person's school
only when an import or the user says so. Nothing is scraped from LinkedIn.

## Decision

1. `Person.schools` (new column, migration) holds the schools a person is known to have attended,
   filled from an import with a school, college, university, or alma mater column, and by
   `hub people tag <id> --school 'Tufts University' [words]`.
2. **`school_key`** makes "Tufts", "Tufts University", and "Tufts Univ." one school (the word
   "university" is ignored) but keeps "college", so Boston College and Boston University stay
   different. A short built-in alias list covers MIT, UMass, CMU, and so on; `school_aliases` in
   `preferences.yaml` adds the user's own.
3. The user's schools come from `profile.md` education and accepted resume and transcript facts.
4. A same-school person gets "Same school: <school>" and 2 points, the same as an alumni-list member;
   if both apply for the same school they count once. Cards mark them "Same school".
5. With `linkedin_school_slug` in settings, each card offers a link to LinkedIn's alumni search for
   the school and the company. Career Hub **builds the link only and never requests it**.

## Evidence

`tests/modules/network/test_schools.py`.

## Consequences

School names are free text, so odd spellings need an alias. Existing people have no schools until
a new import or a tag provides them.
