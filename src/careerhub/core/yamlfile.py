"""Edit a YAML file the user wrote without losing their comments and layout (CH-180).

Career Hub changes `preferences.yaml` and `rubric.yaml` only when the user accepts a proposal.
Rewriting the file from parsed data would drop comments, blank lines, quoting, and grouping.
`edit_yaml_text` instead loads the original text in ruamel.yaml's round-trip mode, changes only
the keys and items that differ from the new data, and writes it back. Reading stays with PyYAML
(`profile/files.py`); ruamel is used only here, for writing.

Safety net: the edited text is parsed again and must equal the new data exactly. If it does not
(or the original cannot be round-tripped), the caller gets a plain dump of the new data instead,
so the change is never wrong, only less tidy.
"""

from __future__ import annotations

import difflib
import io
import json
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def _indent_style(text: str) -> tuple[int, int, int]:
    """Guess (mapping, sequence, offset) indentation from how the file already writes lists."""
    lines = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    for before, after in zip(lines, lines[1:], strict=False):
        key = re.match(r"^( *)[^\s#-][^:]*:\s*(#.*)?$", before)
        item = re.match(r"^( *)- ", after)
        if key and item:
            offset = len(item.group(1)) - len(key.group(1))
            if offset > 0:
                return 2, offset + 2, offset
            return 2, 2, 0
    return 2, 2, 0


def _sync(old: Any, new: Any) -> None:
    """Change `old` (a ruamel mapping or list) in place until it holds the same data as `new`."""
    if isinstance(old, MutableMapping) and isinstance(new, Mapping):
        for key in [k for k in old if k not in new]:
            del old[key]
        for key, value in new.items():
            if key in old and _same_kind(old[key], value):
                _sync(old[key], value)
            elif key not in old or old[key] != value:
                old[key] = value
    elif isinstance(old, list) and isinstance(new, list):
        _sync_list(old, new)


def _sync_list(old: list[Any], new: list[Any]) -> None:
    """Edit a list by value, so each item keeps its own comment when others come and go.

    Items are matched by equality: an unchanged item is left alone, a removed one is deleted with
    its comment, an added one is inserted, and a changed mapping or list that sits where a
    similar one was is edited in place (so a reweighed rubric criterion keeps its comment).
    """
    matcher = difflib.SequenceMatcher(
        a=[json.dumps(x, sort_keys=True, default=str) for x in old],
        b=[json.dumps(x, sort_keys=True, default=str) for x in new],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
        if tag == "delete":
            del old[i1:i2]
        elif tag == "insert":
            for offset, value in enumerate(new[j1:j2]):
                old.insert(i1 + offset, value)
        elif tag == "replace":
            for offset in range(min(i2 - i1, j2 - j1)):
                value = new[j1 + offset]
                if _same_kind(old[i1 + offset], value):
                    _sync(old[i1 + offset], value)
                else:
                    old[i1 + offset] = value
            if i2 - i1 > j2 - j1:
                del old[i1 + (j2 - j1) : i2]
            else:
                for offset, value in enumerate(new[j1 + (i2 - i1) : j2]):
                    old.insert(i2 + offset, value)


def _same_kind(a: Any, b: Any) -> bool:
    """Say whether both are mappings or both are lists (so they can be edited in place)."""
    return (isinstance(a, Mapping) and isinstance(b, Mapping)) or (
        isinstance(a, list) and isinstance(b, list)
    )


def _plain_dump(new: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(new), sort_keys=False, allow_unicode=True)


def edit_yaml_text(original: str | None, new: Mapping[str, Any]) -> str:
    """Return `original` changed to hold `new`, keeping comments, blank lines, and order.

    With no original text (a new file) this is a plain dump.
    """
    if not original or not original.strip():
        return _plain_dump(new)
    try:
        engine = YAML()
        engine.preserve_quotes = True
        engine.width = 4096  # never re-wrap the user's long lines
        mapping, sequence, offset = _indent_style(original)
        engine.indent(mapping=mapping, sequence=sequence, offset=offset)
        document = engine.load(original)
        if not isinstance(document, Mapping):
            return _plain_dump(new)
        _sync(document, new)
        out = io.StringIO()
        engine.dump(document, out)
        text = out.getvalue()
        if yaml.safe_load(text) != yaml.safe_load(_plain_dump(new)):
            return _plain_dump(new)
    except (YAMLError, yaml.YAMLError, ValueError, TypeError):
        return _plain_dump(new)
    return text
