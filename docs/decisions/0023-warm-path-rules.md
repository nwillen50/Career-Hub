# 0023. Warm-path ranking rules

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-73, part of CH-28 (step 7)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

PRD: Network finds "who you know at each company, and the strongest warm path." The user should be
able to see why someone ranks where they do.

## Decision

1. **Plain points, no model**: the user's own words in `how_known` 3; an imported alumni group 2;
   a LinkedIn connection 1; contact within the last year 1; a recruiter or talent title 1.
   Reasons are listed next to the points, in plain English.
2. **Cumulative but each fact once**: a person can earn from several facts (a tagged LinkedIn
   connection scores 4); several alumni groups count once.
3. **The user's own words come first** because only the user knows a real relationship; imported
   facts are weaker evidence.
4. **People with no facts are still listed, last**, since knowing someone there beats nothing.
5. `how_known` may hold several `;`-separated notes; those starting `alumni:` are group tags
   (ADR-0022), the rest are the user's words.
6. `hub paths <company>` lists everyone known there. Second-degree connections and past email contact
   are later work (PRD: Later).

## Evidence

`tests/modules/network/test_warm_paths.py`: ordering, recency and recruiter points, mixed tags,
people with no facts, limit, company scoping, company lookup, and the command.

## Alternatives considered

- **A learned model.** Opaque, and there is no outcome data yet to learn from.
- **Treat alumni as stronger than a bare connection by a bigger gap.** The weights are a first
  guess; the PRD's "warm paths used" metric will show whether they help.

## Consequences

- Weights live in one table (`_score`); changing them is a small edit plus tests.
- Titles are matched by substring ("recruit", "talent"), which can misfire on odd titles.
