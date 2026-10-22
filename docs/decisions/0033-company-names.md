# 0033. One company key for every spelling of a name

- Status: Accepted (built-in alias list flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-86, for epic CH-21 (step 4)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

`company_key` only lowercased, so "Microsoft", "Microsoft.com", and "Microsoft Corporation" were
three companies: duplicates split across sources, excluded companies slipped through, and warm
paths were missed.

## Decision

1. **`company_key`** lowercases; turns "&" into "and"; drops a leading "www." and "the", a trailing
   web suffix (.com .io .ai .co .net .org), and trailing legal words (inc, corp, corporation, co,
   company, llc, ltd, limited, plc, gmbh, ag, sa, lp, llp), repeatedly; removes apostrophes and dots;
   turns other punctuation into spaces. A name that would become empty keeps its last word. There is
   no fuzzy matching, so two real companies are never merged by accident.
2. **Built-in aliases** for names that differ completely: Alphabet to Google, Facebook and Meta
   Platforms to Meta, AWS to Amazon Web Services, Walt Disney to Disney, International Business
   Machines to IBM.
3. **`core/companies.py`** is the one place that creates companies (`get_or_create_company`, used by
   the job store, LinkedIn import, and alumni import) and looks them up (`find_companies`: exact key
   first, otherwise whole words only, so "Meta" never finds "Metabase"; several hits are all
   returned). `hub paths` lists the candidates when a name is ambiguous instead of picking one.
4. **A migration** recomputes every key and merges companies that now share one: the earliest row
   is kept; jobs and people are repointed; domains and board ids are combined. It cannot be undone.
5. Not in this change (CH-111): user-editable aliases in `preferences.yaml`, and suggestions for
   near misses.

## Evidence

`tests/core/test_company_names.py`: the Microsoft variants, AT&T, Disney, Meta vs Metabase,
odd and empty names, ambiguity, and the migration on a database with duplicates.

## Consequences

Anything that stored a company key outside the `company` table must use `company_key`. Changing the
rules later needs a new migration.
