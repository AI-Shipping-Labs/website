"""Shared datetime serialization helpers for API payloads."""


def isoformat_or_none(value):
    """Return an ISO-8601 string for a value, or ``None`` for null."""
    if value is None:
        return None
    return value.isoformat()
