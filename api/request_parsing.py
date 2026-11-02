"""Shared query-parameter parsers for JSON API views."""

from datetime import date, datetime

from django.utils.dateparse import parse_datetime

from accounts.lifecycle import ACCOUNT_LIFECYCLE_VALUES
from api.safety import error_response

LIMIT_DEFAULT = 50
LIMIT_MAX = 200


def normalize_emails(raw):
    """Trim, lowercase, and deduplicate emails while preserving input order."""
    seen = set()
    normalized = []
    for email in raw:
        if not isinstance(email, str):
            continue
        cleaned = email.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def parse_iso_date(raw):
    """Parse an ISO date string, returning ``None`` when it is invalid."""
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def coerce_datetime(value):
    """Parse an ISO datetime string and pass through other datetime values."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return parse_datetime(value)
    return value


def parse_limit(raw, *, default=LIMIT_DEFAULT, field="limit", maximum=LIMIT_MAX):
    """Parse a positive page size, capped at ``maximum`` (API-wide default)."""
    if raw is None or raw == "":
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    if value < 1:
        return None, error_response(
            f"{field} must be a positive integer",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return min(value, maximum), None


def parse_offset(raw, *, field="offset"):
    """Parse a non-negative pagination offset."""
    if raw is None or raw == "":
        return 0, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, error_response(
            f"Invalid integer: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    if value < 0:
        return None, error_response(
            f"{field} must be a non-negative integer",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return value, None


def parse_since(raw, *, field="since"):
    """Parse an optional ISO-8601 datetime, accepting a trailing ``Z``."""
    if raw is None or raw == "":
        return None, None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None, error_response(
            f"Invalid ISO-8601 datetime: {raw!r}",
            "validation_error",
            status=422,
            details={"field": field, "value": raw},
        )
    return value, None


def parse_account_lifecycle(raw):
    """Parse an optional account-lifecycle filter."""
    value = (raw or "").strip()
    if not value:
        return "", None
    if value in ACCOUNT_LIFECYCLE_VALUES:
        return value, None
    return None, error_response(
        f"Invalid account_lifecycle: {raw!r}",
        "validation_error",
        status=422,
        details={
            "field": "account_lifecycle",
            "value": raw,
            "allowed": list(ACCOUNT_LIFECYCLE_VALUES),
        },
    )
