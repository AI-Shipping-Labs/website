"""Shared Slack user-ID normalization and validation for operator writes."""

import re

SLACK_USER_ID_PATTERN = re.compile(r"^[UW][A-Z0-9]{2,}$")


def normalize_slack_user_id(value):
    """Return the canonical uppercase Slack user ID, allowing blank clears."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    return value.strip().upper()


def is_valid_slack_user_id(value):
    """Return whether a normalized value is blank or a supported Slack ID."""
    return isinstance(value, str) and (
        value == "" or bool(SLACK_USER_ID_PATTERN.fullmatch(value))
    )
