"""Versioned prompt files, named `<name>_v<version>.md`; changing a prompt means a new version.

Prompts use `{{placeholder}}` markers (not `str.format`, so JSON examples with braces are safe).
Each prompt has tests (`tests/test_prompts.py`) and each result records the version that made it.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPT_DIR = Path(__file__).parent
_MARKER = re.compile(r"\{\{([a-z_]+)\}\}")


def load_prompt(name: str, version: int) -> str:
    """Return the text of prompt `name` at `version` (raises FileNotFoundError if missing)."""
    return (PROMPT_DIR / f"{name}_v{version}.md").read_text(encoding="utf-8")


def render(template: str, **values: str) -> str:
    """Fill each `{{key}}` in `template` with its value; a marker with no value is an error.

    Values are inserted in one pass, so text inside a value (such as a posting) is never treated
    as a marker.
    """
    missing = set(_MARKER.findall(template)) - values.keys()
    if missing:
        raise ValueError(f"The prompt needs values for: {', '.join(sorted(missing))}.")
    return _MARKER.sub(lambda match: values.get(match.group(1), match.group(0)), template)
