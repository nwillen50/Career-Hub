"""Finds and reads the profile files in the data folder.

Layout (under the data folder from `careerhub.core.paths`)::

    profile/profile.md          front matter (YAML) between --- lines, then free text
    profile/preferences.yaml    hard constraints, timeline, weekly time budget

A file that doesn't exist is not an error: the loaders return None and the caller reports it. A
file that exists but is malformed raises `ProfileFileError`, naming the fields that are wrong and
never their values, since values are personal.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from careerhub.core.paths import data_dir
from careerhub.profile.models import Preferences, ProfileDoc

PROFILE_DIRNAME = "profile"
PROFILE_DOC_FILENAME = "profile.md"
PREFERENCES_FILENAME = "preferences.yaml"

# Front matter: a leading "---" line, YAML, a closing "---" line, then the body.
_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z", re.DOTALL)


class ProfileFileError(Exception):
    """A profile file exists but can't be used. The message never contains file contents."""


def profile_doc_path() -> Path:
    """Return where `profile.md` should be (it may not exist)."""
    return data_dir() / PROFILE_DIRNAME / PROFILE_DOC_FILENAME


def preferences_path() -> Path:
    """Return where `preferences.yaml` should be (it may not exist)."""
    return data_dir() / PROFILE_DIRNAME / PREFERENCES_FILENAME


def load_profile_doc(path: Path | None = None) -> ProfileDoc | None:
    """Read `profile.md`. Returns None if the file doesn't exist."""
    path = path or profile_doc_path()
    if not path.exists():
        return None
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        raise ProfileFileError(
            f"{path.name} must start with a line of three dashes, YAML fields, "
            "then another line of three dashes."
        )
    fields = parse_yaml(match.group(1), path.name)
    fields["about"] = match.group(2).strip()
    return validate_model(ProfileDoc, fields, path.name)


def load_preferences(path: Path | None = None) -> Preferences | None:
    """Read `preferences.yaml`. Returns None if the file doesn't exist."""
    path = path or preferences_path()
    if not path.exists():
        return None
    return validate_model(
        Preferences, parse_yaml(path.read_text(encoding="utf-8"), path.name), path.name
    )


def parse_yaml(text: str, filename: str) -> dict[str, Any]:
    """Parse YAML safely into a dict (an empty file is an empty dict)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        line = getattr(getattr(error, "problem_mark", None), "line", None)
        where = f" (line {line + 1})" if isinstance(line, int) else ""
        raise ProfileFileError(f"{filename} is not valid YAML{where}.") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ProfileFileError(f"{filename} must contain named fields, not a list or a value.")
    return {str(key): value for key, value in data.items()}


def validate_model[ModelT: BaseModel](
    model: type[ModelT], fields: dict[str, Any], filename: str
) -> ModelT:
    """Build `model` from `fields`, reporting problems by field name only."""
    try:
        return model.model_validate(fields)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ProfileFileError(f"{filename} has invalid fields: {problems}") from None
