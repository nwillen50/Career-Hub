# 0047. Editing the user's YAML keeps comments and layout (ruamel.yaml)

- Status: Accepted (new dependency flagged for Nicholas)
- Date: 2026-10-24
- Ticket: CH-180 (from CH-159 and CH-160)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

Accepting a proposal rewrote `preferences.yaml` or `rubric.yaml` from parsed data, which dropped
the user's comments, blank lines, quoting, and sometimes key order.

## Decision

1. Add **ruamel.yaml** (MIT licence) as a dependency. It is used only to write: `core/yamlfile.py`
   `edit_yaml_text` loads the original text in round-trip mode, changes only the keys and list
   items that differ from the new data, and dumps it back. Both proposal writers use it
   (`proposals.accept`, `rubric_proposals.accept_rubric`); the backup `.before-<time>` copy and the
   Pydantic validation before saving are unchanged.
2. **Reading stays with PyYAML.** One library for both would not be simpler: ruamel's round-trip
   objects are not plain dicts, and every reader and validator already takes plain data.
3. **Safety net:** the edited text is parsed again and must equal the new data exactly; if not, or
   if the original cannot be round-tripped (broken YAML), the writer falls back to the old plain
   dump. The change is never wrong, only sometimes less tidy.
4. Indentation of lists is guessed from the file (`- item` indented under its key or not).

## Known limits

- ruamel.yaml drops a blank line that directly follows a one-line `{...}` list item (the style
  `rubric.yaml` is usually written in). Comments, ordering, quoting, and blank lines elsewhere
  survive.
- Lists are edited by value, so each item keeps its own comment. When a whole mapping key is
  deleted, a comment line above it stays and then sits above the next key.
- `targets.yaml` (a list Career Hub itself imports and edits) is still written with a plain dump;
  it is not a hand-annotated file.

## Consequences

One new runtime dependency (pure Python, MIT). Any later writer of a user YAML file should use
`edit_yaml_text`.
