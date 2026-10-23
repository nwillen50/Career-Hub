"""Remember the column mapping used for an alumni file, by file name (CH-116).

A school sends the same export every term under the same file name. Once the user has told
`hub alumni import` which column is which (`--map`, `--sheet`, `--group`), the choice is saved in
`alumni-mappings.json` in the data folder (private, like the other personal files) and used again
the next time a file with that name is imported. Only header names and field names are stored,
never people. Explicit flags win over the saved choice and update it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from careerhub.core.files import write_private
from careerhub.core.paths import data_dir

MAPPINGS_FILENAME = "alumni-mappings.json"


@dataclass(frozen=True)
class SavedMapping:
    """What was chosen for one file name."""

    mapping: dict[str, str] = field(default_factory=dict)
    sheet: str | None = None
    group: str | None = None


def mappings_path() -> Path:
    """Return where the saved mappings live (the file may not exist)."""
    return data_dir() / MAPPINGS_FILENAME


def _load_all() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(mappings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def load_mapping(file_name: str) -> SavedMapping | None:
    """Return the saved choice for `file_name`, or None."""
    entry = _load_all().get(file_name)
    if entry is None:
        return None
    raw = entry.get("mapping")
    mapping = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    sheet, group = entry.get("sheet"), entry.get("group")
    return SavedMapping(
        mapping,
        sheet if isinstance(sheet, str) else None,
        group if isinstance(group, str) else None,
    )


def save_mapping(file_name: str, saved: SavedMapping) -> None:
    """Save (or replace) the choice for `file_name`."""
    everything = _load_all()
    everything[file_name] = {"mapping": saved.mapping, "sheet": saved.sheet, "group": saved.group}
    path = mappings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(everything, indent=1, sort_keys=True) + "\n")


def forget_mapping(file_name: str) -> bool:
    """Remove the saved choice for `file_name`; return whether there was one."""
    everything = _load_all()
    if file_name not in everything:
        return False
    del everything[file_name]
    write_private(mappings_path(), json.dumps(everything, indent=1, sort_keys=True) + "\n")
    return True


def list_mappings() -> dict[str, list[str]]:
    """Return each saved file name with the fields it maps (no headers, no people)."""
    saved = (load_mapping(name) for name in _load_all())
    return {
        name: sorted(item.mapping) for name, item in zip(_load_all(), saved, strict=True) if item
    }
