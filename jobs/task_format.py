"""Shared formatting helpers for completed django-q task values."""

from __future__ import annotations

import pprint

ERROR_SUMMARY_MAX_LENGTH = 160
NO_ERROR_DETAILS_PLACEHOLDER = "No error details"


def format_task_value(value):
    """Pretty-print a task value for operator HTML and JSON surfaces."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return pprint.pformat(value, width=100, sort_dicts=False)
    except Exception:  # pragma: no cover - defensive
        return repr(value)


def looks_like_traceback(text):
    """Return whether text has a leading or embedded Python traceback."""
    if not isinstance(text, str):
        return False
    return text.startswith("Traceback") or "\nTraceback (most recent call last):" in text


def extract_error_summary(error_message):
    """Return the most informative bounded line from a task error."""
    if not error_message:
        return NO_ERROR_DETAILS_PLACEHOLDER

    nonblank_lines = [line.strip() for line in error_message.splitlines() if line.strip()]
    if not nonblank_lines:
        return NO_ERROR_DETAILS_PLACEHOLDER

    if looks_like_traceback(error_message):
        summary_line = nonblank_lines[-1]
    else:
        summary_line = nonblank_lines[0]

    if len(summary_line) > ERROR_SUMMARY_MAX_LENGTH:
        summary_line = summary_line[: ERROR_SUMMARY_MAX_LENGTH - 3] + "..."
    return summary_line
