"""Background-task enqueue helpers for community lifecycle events.

Each helper wraps :func:`jobs.tasks.async_task` so payment handlers can
fire community invite/remove/reactivate without having to know about the
jobs system. Failures here NEVER propagate to the webhook caller — the
user's payment record is authoritative, and a missed enqueue is a
log-warning concern.

Logger calls go through the ``payments.services`` package so tests that
patch ``payments.services.logger`` see the writes.
"""

from payments import services as _services


def _community_invite(user):
    """Invite a user to the community via a background task."""
    try:
        from jobs.tasks import async_task, build_task_name
        async_task(
            "community.tasks.hooks.community_invite_task",
            user_id=user.pk,
            task_name=build_task_name(
                "Invite community member",
                f"user #{user.pk}",
                "payment lifecycle",
            ),
        )
    except Exception:
        # Intentional broad catch: an enqueue failure (Redis down,
        # django-q schema mismatch, DB hiccup, settings drift) must NOT
        # break the webhook caller — the user's payment has already
        # been recorded. The community invite is best-effort and can be
        # re-driven by support tooling.
        _services.logger.exception(
            "Failed to enqueue community invite for user=%s", user.email,
        )


def _community_reactivate(user):
    """Reactivate a user in the community via a background task."""
    try:
        from jobs.tasks import async_task, build_task_name
        async_task(
            "community.tasks.hooks.community_reactivate_task",
            user_id=user.pk,
            task_name=build_task_name(
                "Reactivate community member",
                f"user #{user.pk}",
                "payment lifecycle",
            ),
        )
    except Exception:
        # Intentional broad catch: see ``_community_invite``. The
        # webhook caller already committed the tier upgrade.
        _services.logger.exception(
            "Failed to enqueue community reactivate for user=%s", user.email,
        )


def _community_remove(user):
    """Remove a user from the community via a background task."""
    try:
        from jobs.tasks import async_task, build_task_name
        async_task(
            "community.tasks.hooks.community_remove_task",
            user_id=user.pk,
            task_name=build_task_name(
                "Remove community member",
                f"user #{user.pk}",
                "payment lifecycle",
            ),
        )
    except Exception:
        # Intentional broad catch: see ``_community_invite``. The
        # webhook caller already committed the downgrade.
        _services.logger.exception(
            "Failed to enqueue community remove for user=%s", user.email,
        )


def _community_schedule_removal(user):
    """Schedule community removal at billing_period_end via a background task."""
    try:
        from jobs.tasks import async_task, build_task_name
        async_task(
            "community.tasks.removal.scheduled_community_removal",
            user_id=user.pk,
            task_name=build_task_name(
                "Schedule community removal",
                f"user #{user.pk}",
                "payment lifecycle",
            ),
        )
    except Exception:
        # Intentional broad catch: see ``_community_invite``. The
        # webhook caller already committed the cancel-at-period-end
        # state; on-call can replay the schedule manually if needed.
        _services.logger.exception(
            "Failed to schedule community removal for user=%s", user.email,
        )
