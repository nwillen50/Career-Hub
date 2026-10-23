"""Decide when a change to the profile is big enough to re-score and re-tailor jobs (CH-115).

Scoring costs money, so a fixed typo or a reworded sentence should not re-score every job. The last
scoring run's profile is kept as a normalized snapshot (`profile/scored-snapshot.json`, private, in
the data folder). Each night the current profile is compared with that snapshot:

- **Lists** (education, skills, interests, preferences such as locations and dates): an item added
  or removed, or changed by more than `typo_distance` letters, is a meaningful change.
- **Free text** (About, accepted resume facts): meaningful once at least `free_percent` of the words
  have changed since the snapshot. Small edits add up because the snapshot only moves when a
  meaningful change is found.

A meaningful change moves the snapshot forward and raises its `epoch`; scoring and tailoring put the
epoch, not the profile text, in their input hash. `hub score --all` raises the epoch by hand.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from careerhub.core.files import write_private
from careerhub.core.paths import data_dir

SNAPSHOT_FILENAME = "scored-snapshot.json"
_NOT_WORD = re.compile(r"[^\w\s]")
LIST_LABELS = {
    "education": "education",
    "skills": "skills",
    "interests": "interests",
    "preferences": "preferences (locations, work modes, dates, salary)",
}


def snapshot_path() -> Path:
    """Return where the snapshot is stored (it may not exist)."""
    return data_dir() / "profile" / SNAPSHOT_FILENAME


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, and collapse spaces, so formatting changes are not changes."""
    return " ".join(_NOT_WORD.sub(" ", text.lower()).split())


def build_snapshot(
    education: Sequence[str],
    skills: Sequence[str],
    interests: Sequence[str],
    preferences: Sequence[str],
    about: str,
    facts: str,
) -> dict[str, list[str]]:
    """Normalize the profile into lists of items (sorted) and lists of words (kept in order)."""
    lists = {
        "education": education,
        "skills": skills,
        "interests": interests,
        "preferences": preferences,
    }
    snapshot = {
        name: sorted({n for n in map(normalize, items) if n}) for name, items in lists.items()
    }
    snapshot["about_words"] = normalize(about).split()
    snapshot["facts_words"] = normalize(facts).split()
    return snapshot


def edit_distance(a: str, b: str) -> int:
    """Return the number of single-letter edits that turn `a` into `b` (Levenshtein)."""
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        row = [i]
        for j, ch_b in enumerate(b, start=1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ch_a != ch_b)))
        previous = row
    return previous[-1]


def _list_changes(old: Sequence[str], new: Sequence[str], typo_distance: int) -> tuple[int, int]:
    """Count items added and removed, treating an item within `typo_distance` as the same one."""
    unmatched = list(new)
    removed = 0
    for item in old:
        match = next((n for n in unmatched if edit_distance(item, n) <= typo_distance), None)
        if match is None:
            removed += 1
        else:
            unmatched.remove(match)
    return len(unmatched), removed


def _word_change_percent(old: Sequence[str], new: Sequence[str]) -> float:
    """Return how many words changed, as a percent of the old text (100 if it was empty)."""
    if not old and not new:
        return 0.0
    if not old:
        return 100.0
    before, after = Counter(old), Counter(new)
    changed = max(sum((after - before).values()), sum((before - after).values()))
    return 100.0 * changed / len(old)


def meaningful_changes(
    old: Mapping[str, list[str]],
    new: Mapping[str, list[str]],
    typo_distance: int,
    free_percent: float,
) -> list[str]:
    """Return plain-words reasons the profile changed enough to re-score (empty if it did not)."""
    reasons: list[str] = []
    for name, label in LIST_LABELS.items():
        added, removed = _list_changes(old.get(name, []), new.get(name, []), typo_distance)
        if added or removed:
            parts = [f"{n} {what}" for n, what in ((added, "added"), (removed, "removed")) if n]
            reasons.append(f"{label}: {' and '.join(parts)}")
    for name, label in (("about_words", "About"), ("facts_words", "accepted resume facts")):
        percent = _word_change_percent(old.get(name, []), new.get(name, []))
        if percent >= free_percent and percent > 0:
            reasons.append(f"{label}: about {percent:.0f}% of the words changed")
    return reasons


@dataclass(frozen=True)
class Fingerprint:
    """The result of comparing the profile with the last scoring run's snapshot."""

    epoch: int
    reasons: list[str] = field(default_factory=list)
    first_run: bool = False

    @property
    def changed(self) -> bool:
        """Say whether jobs will be re-scored because of the profile."""
        return bool(self.reasons) or self.first_run


def _load(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("epoch"), int) else None


def check(
    current: Mapping[str, list[str]],
    typo_distance: int,
    free_percent: float,
    save: bool = True,
    path: Path | None = None,
) -> Fingerprint:
    """Compare `current` with the stored snapshot and return the epoch scoring should use.

    With `save=True` (the nightly run) a meaningful change moves the snapshot forward and raises the
    epoch; with `save=False` (`hub profile show`) nothing is written, and the result says what would
    happen. The first run just stores the snapshot (jobs have no score yet, so nothing is wasted).
    """
    path = path or snapshot_path()
    stored = _load(path)
    if stored is None:
        if save:
            _write(path, current, 0)
        return Fingerprint(0, [], first_run=True)
    epoch = int(str(stored["epoch"]))
    old = stored.get("snapshot")
    old_map = {k: [str(x) for x in v] for k, v in old.items()} if isinstance(old, dict) else {}
    reasons = meaningful_changes(old_map, current, typo_distance, free_percent)
    if reasons and save:
        epoch += 1
        _write(path, current, epoch)
    return Fingerprint(epoch + (0 if save else bool(reasons)), reasons)


def force_rescore(path: Path | None = None) -> int:
    """Raise the epoch so every job is scored again (`hub score --all`); return the new epoch."""
    path = path or snapshot_path()
    stored = _load(path)
    if stored is None:
        return 0  # nothing has been scored yet; the first run scores everything anyway
    snapshot = stored.get("snapshot")
    epoch = int(str(stored["epoch"])) + 1
    _write(path, snapshot if isinstance(snapshot, dict) else {}, epoch)
    return epoch


def _write(path: Path, snapshot: Mapping[str, object], epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps({"epoch": epoch, "snapshot": snapshot}, indent=1) + "\n")
