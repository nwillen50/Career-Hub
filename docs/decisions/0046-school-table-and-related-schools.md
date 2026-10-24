# 0046. A reviewed school table, and "related school" ties

- Status: Accepted (seed table; the IPEDS import is CH-199)
- Date: 2026-10-24
- Ticket: CH-179, for epic CH-28 (step 7). Builds on CH-155
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver, results decided Oct 23)

## Context

`school_key` drops "University" and "of" and keeps a short alias list, so similar names are hit or
miss: "Penn State" and "Pennsylvania State University" did not match; "UC Davis" did not match
"University of California, Davis"; PSU means three schools; branch campuses (Penn State Behrend)
are neither the same school nor strangers.

## Decision

1. A bundled table, `modules/network/schools.yaml`, lists official names, aliases, branch campuses
   (`branch_of`), and systems of separate universities (`system`). Matching goes through it first;
   names it does not hold use the old word rules. User school aliases in `preferences.yaml` win
   over both.
2. Four results, strongest first: **same school** (2 points, "Same school"); **branch campus**
   (1 point, "Branch campus: Penn State"); **same system** (0 points but ranks above no tie,
   "Same system: University of California"); none. A bare system name ("University of California")
   matches no campus.
3. An ambiguous short name (PSU, USC, UW, UM, WashU) matches nothing on its own. `hub profile show`
   lists your schools that are ambiguous and the `school_aliases` entry that settles each.
4. **Data source:** the ticket asked for IPEDS (US Department of Education, public domain). The
   cloud sandbox cannot reach nces.ed.gov (403), so this change ships a reviewed seed of about 70
   schools that covers the confusable cases in the ticket. Importing IPEDS into the same format is
   CH-199 and needs a session that can download the file. IPEDS names branch campuses and
   separate universities alike, so the short reviewed list of systems stays by hand either way.

## Consequences

A school outside the seed behaves as before. The table is data, so adding a school is a YAML edit
plus a test.
