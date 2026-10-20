from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest

from careerhub.core.logging import LOGGER_NAME, configure_logging, redact

KEY = "ab" * 32


@pytest.mark.parametrize(
    "text, hidden",
    [
        ("contact jane.doe@example.com today", "jane.doe"),
        ("call +1 (555) 123-4567 now", "123-4567"),
        ("call 555.123.4567", "123.4567"),
        (f"key is x'{KEY}'", KEY),
        (f"raw {KEY}", KEY),
        ("fetch https://user:hunter2@example.com/x", "hunter2"),
        ("token=abc123xyz", "abc123xyz"),
        ("password: s3cret", "s3cret"),
    ],
)
def test_redact_hides_personal_details(text: str, hidden: str) -> None:
    assert hidden not in redact(text)


def test_redact_leaves_ordinary_text_alone() -> None:
    text = "Fetched 42 postings from 3 sources in 12.5 seconds (run 2026-10-20)"
    assert redact(text) == text


@pytest.fixture(autouse=True)
def _reset_logger() -> object:
    yield
    configure_logging()  # drops file handlers so tmp files can be removed
    logging.getLogger(LOGGER_NAME).handlers.clear()


def test_log_file_never_contains_personal_details(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "careerhub.log"
    logger = configure_logging("DEBUG", log_file)
    child = logging.getLogger("careerhub.test")
    child.info("hello %s", "jane.doe@example.com")
    try:
        raise ValueError("bad row for 555-123-4567")
    except ValueError:
        child.exception("failed with key %s", KEY)
    for handler in logger.handlers:
        handler.flush()
    text = log_file.read_text()
    assert "hello [email]" in text
    for secret in ("jane.doe", "555-123-4567", KEY):
        assert secret not in text
    assert "ValueError" in text  # the traceback is kept, redacted


def test_log_file_and_folder_are_private(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "careerhub.log"
    configure_logging("INFO", log_file)
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_file.parent.stat().st_mode) == 0o700


def test_level_is_respected_and_reconfiguring_replaces_handlers(tmp_path: Path) -> None:
    configure_logging("WARNING", tmp_path / "a.log")
    logger = configure_logging("ERROR", tmp_path / "b.log")
    assert logger.level == logging.ERROR
    assert len(logger.handlers) == 2
