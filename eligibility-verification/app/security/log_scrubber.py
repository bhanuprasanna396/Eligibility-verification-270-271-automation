"""
Logging filter that redacts PHI patterns from log records before they are
emitted to any handler (file, stdout, SIEM, etc.).

Attach to the root logger in main.py:
    logging.getLogger().addFilter(PhiLogScrubber())

Patterns covered:
  - US Social Security Numbers  (123-45-6789)
  - ISO dates                   (1985-01-15)
  - Member IDs starting with MEM (MEM001, MEM-ABC-123)
  - Email addresses
"""
import logging
import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN-REDACTED]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "[DOB-REDACTED]"),
    (re.compile(r"\bMEM[\w-]+\b"), "[MEMBER-ID-REDACTED]"),
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL-REDACTED]",
    ),
]


def _scrub(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


class PhiLogScrubber(logging.Filter):
    """Logging filter that redacts known PHI patterns from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _scrub(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(_scrub(str(a)) for a in record.args)
        return True
