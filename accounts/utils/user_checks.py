"""Defensive user-state checks shared across auth-sensitive helpers."""

from __future__ import annotations


def is_authenticated_user(user) -> bool:
    """Return ``True`` only for authenticated user-like objects."""
    if user is None:
        return False
    return bool(getattr(user, 'is_authenticated', False))


def is_staff_user(user) -> bool:
    """Return ``True`` only for authenticated staff user-like objects."""
    return is_authenticated_user(user) and bool(getattr(user, 'is_staff', False))
