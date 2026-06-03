"""Account activation helpers (issue #768).

A user becomes "activated" the first time they do a real platform
action: verify their email after an email+password signup, link an
OAuth identity, pay for a tier, post a comment, register for an event,
complete a course unit, create a sprint plan, or have Slack workspace
membership confirmed via OAuth.

The flag is orthogonal to ``signup_source`` (how the row was created):
``signup_source`` is set once at row creation and never changes;
``account_activated`` flips False → True the first time any of the
above happens. Once True it stays True forever — the helper is
idempotent so downstream call sites can fire it on every trigger
without worrying about double-saves.

Newsletter subscribers who only verify their email never get activated
here — they never set a password and have never taken a platform
action beyond confirming the inbox. They show up as
``signup_source='newsletter'`` + ``account_activated=False`` until
they do something else.
"""

from __future__ import annotations


def mark_activated(user) -> bool:
    """Flip ``account_activated`` to True on ``user`` if not already set.

    Idempotent: a no-op when ``user.account_activated`` is already True.
    Returns True iff the row was flipped by this call (useful for
    audit / logging in the future, but no caller currently inspects
    the return value).

    Skips users without a primary key — those are not yet persisted and
    flipping the bit would be lost anyway. This keeps the helper safe
    to call from code paths that touch un-saved ``User`` instances
    (e.g. signal handlers that fire before the row commits).
    """
    if user is None:
        return False
    if not getattr(user, "pk", None):
        return False
    if user.account_activated:
        return False
    user.account_activated = True
    user.save(update_fields=["account_activated"])
    return True


def mark_email_verified(user) -> bool:
    """Flip ``email_verified`` to True on ``user`` if not already set.

    This mirrors ``mark_activated`` for idempotent payment/OAuth hooks:
    unsaved users are ignored, verified users are a no-op, and the only
    database write is a focused ``email_verified`` update.
    """
    if user is None:
        return False
    if not getattr(user, "pk", None):
        return False
    if user.email_verified:
        return False
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    return True
