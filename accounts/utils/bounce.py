"""Shared bounce-state helpers (issues #766, #784).

Single source of truth for the structured bounce-state writes that flip
``User.bounce_state`` / ``bounce_recorded_at`` / ``last_bounce_diagnostic``
and (for permanent bounces) ``unsubscribed`` / (for soft bounces)
``soft_bounce_count``.

Two call sites consume these helpers:

- ``api/views/ses_events.py``      -- inbound SNS-delivered SES webhook.
- ``api/views/users.py``           -- operator-triggered
  ``POST /api/users/<email>/mark-bounced``.

Keeping the writes here -- rather than duplicating them between the
webhook and the API -- removes the drift risk that would otherwise
accumulate every time someone tweaks the bounce side-effects (e.g.
adding a new field to clear, changing the soft-bounce threshold, or
extending the eager-purge fields).

The helpers are deliberately framework-agnostic (no request, no
response, no transaction). The caller is expected to wrap the call in
a ``transaction.atomic()`` with the appropriate ``select_for_update``
lock when concurrent writes are possible.
"""

from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


# Number of transient (soft) bounces tolerated before we treat the user as
# permanently bounced. Three matches what most ESPs use for soft-fail
# tolerance: a single "mailbox full" hiccup shouldn't unsubscribe anyone,
# but three consecutive failures is a real signal.
SOFT_BOUNCE_THRESHOLD = 3

# Cap on the diagnostic text persisted in ``User.last_bounce_diagnostic``
# (issue #766). SES diagnostics are typically a single SMTP line but the
# spec is loose enough that an over-long string could waste storage; 500
# chars is generous for operator triage.
MAX_BOUNCE_DIAGNOSTIC_LEN = 500


def mark_permanent_bounce(user, *, diagnostic=""):
    """Flip ``unsubscribed`` and write the structured permanent-bounce
    fields on ``User`` (issue #766). Idempotent.

    The most recent bounce is the operative one, so
    ``bounce_recorded_at`` is refreshed on every call -- a permanent
    bounce arriving for a row that was already PERMANENT moves the
    timestamp forward.
    """
    user.unsubscribed = True
    user.bounce_state = User.BounceState.PERMANENT
    user.bounce_recorded_at = timezone.now()
    user.last_bounce_diagnostic = (diagnostic or "")[:MAX_BOUNCE_DIAGNOSTIC_LEN]
    user.save(update_fields=[
        "unsubscribed",
        "bounce_state",
        "bounce_recorded_at",
        "last_bounce_diagnostic",
    ])


def record_soft_bounce(user, *, diagnostic=""):
    """Increment ``soft_bounce_count``, flipping at the threshold.

    Returns ``(new_count_after_write, flipped_to_unsubscribed)``. On the
    first soft bounce the row's ``bounce_state`` flips to ``SOFT`` and the
    diagnostic / timestamp are stored; subsequent soft bounces refresh the
    timestamp + diagnostic. When the counter reaches
    ``SOFT_BOUNCE_THRESHOLD`` the row is upgraded to ``PERMANENT`` (issue
    #766) and the counter is reset so the row is reusable if an operator
    manually clears ``unsubscribed`` later.
    """
    diagnostic_trimmed = (diagnostic or "")[:MAX_BOUNCE_DIAGNOSTIC_LEN]
    user.soft_bounce_count = (user.soft_bounce_count or 0) + 1
    now = timezone.now()
    if user.soft_bounce_count >= SOFT_BOUNCE_THRESHOLD:
        user.soft_bounce_count = 0
        user.unsubscribed = True
        user.bounce_state = User.BounceState.PERMANENT
        user.bounce_recorded_at = now
        user.last_bounce_diagnostic = diagnostic_trimmed
        user.save(update_fields=[
            "soft_bounce_count",
            "unsubscribed",
            "bounce_state",
            "bounce_recorded_at",
            "last_bounce_diagnostic",
        ])
        return 0, True
    user.bounce_state = User.BounceState.SOFT
    user.bounce_recorded_at = now
    user.last_bounce_diagnostic = diagnostic_trimmed
    user.save(update_fields=[
        "soft_bounce_count",
        "bounce_state",
        "bounce_recorded_at",
        "last_bounce_diagnostic",
    ])
    return user.soft_bounce_count, False
