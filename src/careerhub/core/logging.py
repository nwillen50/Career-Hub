"""Logging that never writes personal details.

`configure_logging()` sets up the ``careerhub`` logger. Every message passes through
`RedactingFilter`, which replaces email addresses, phone numbers, database keys, and passwords or
tokens in URLs with placeholders *before* anything is written. Redaction is a safety net: code
should still avoid logging personal data in the first place.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

from careerhub.core.paths import data_dir

#: Name of the parent logger; use ``logging.getLogger("careerhub.<module>")`` in each module.
LOGGER_NAME = "careerhub"

#: Patterns to hide, most specific first. Each maps to the placeholder that replaces it.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # A SQLCipher raw key, x'<64 hex>', or a bare 64-character hex key.
    (re.compile(r"x'[0-9a-fA-F]{64}'|\b[0-9a-fA-F]{64}\b"), "[key]"),
    # user:password@ inside a URL.
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@"), "[credentials]@"),
    # token=..., password=..., key=... style pairs.
    (
        re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key)\b(\s*[=:]\s*)\S+"),
        r"\1\2[secret]",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    # Phone numbers: 10+ digits, allowing spaces, dots, dashes, parentheses, and a leading +.
    # A date like 2026-10-20 is not a phone number.
    (re.compile(r"(?<![\w])(?!\d{4}-\d{2}-\d{2}\b)\+?\(?\d[\d\s().-]{8,}\d(?![\w])"), "[phone]"),
)


def redact(text: str) -> str:
    """Return `text` with emails, phone numbers, keys, and credentials replaced by placeholders."""
    for pattern, placeholder in _REDACTIONS:
        text = pattern.sub(placeholder, text)
    return text


class RedactingFilter(logging.Filter):
    """Redacts a record's final message (arguments included) before any handler sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Replace the record's message with its redacted form; always keep the record."""
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            # Tracebacks can quote personal text too; render and redact them now.
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def log_file_path() -> Path:
    """Return the log file's path, inside the data folder."""
    return data_dir() / "logs" / "careerhub.log"


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """Set up the ``careerhub`` logger with redaction, and return it.

    Safe to call more than once: earlier handlers are replaced. With `log_file` the log is also
    written there (rotated at 1 MB, three old files kept), readable only by the user.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        log_file.chmod(0o600)
        handlers.append(file_handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    return logger
