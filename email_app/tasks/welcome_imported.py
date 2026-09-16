"""Background tasks for imported-user welcome emails.

The send goes through the durable package path (A1.2 remainder slice 4):
the import tags and course slugs stay scalar text (#1613) and the worker
resolver mints the password-reset and sign-in links at delivery time. The
``EmailLog`` ``already_sent`` dedupe gate is preserved — the worker writes
the audit row after provider acceptance, so a ``sent`` task result means
the durable delivery exists.
"""

from django.contrib.auth import get_user_model

from email_app.models import EmailLog


def enqueue_imported_welcome_email(user_id):
    """Django-Q scheduled entry point: enqueue the actual send task."""
    from jobs.tasks import async_task, build_task_name

    return async_task(
        "email_app.tasks.welcome_imported.send_imported_welcome_email",
        user_id,
        task_name=build_task_name(
            "Send imported welcome email",
            f"user #{user_id}",
            "import welcome fan-out",
        ),
    )


def send_imported_welcome_email(user_id):
    """Send the imported-user welcome email once per user."""
    from community_base.mail.models import EmailDelivery

    from email_app.package_mail import send_package_mail

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {"status": "skipped", "reason": "missing_user", "user_id": user_id}

    if user.unsubscribed:
        return {"status": "skipped", "reason": "unsubscribed", "user_id": user_id}

    if EmailLog.objects.filter(user=user, email_type="welcome_imported").exists():
        return {"status": "skipped", "reason": "already_sent", "user_id": user_id}

    delivery = send_package_mail(user, "welcome_imported", _build_context(user))
    if delivery.state == EmailDelivery.State.SUPPRESSED:
        return {"status": "skipped", "reason": "unsubscribed", "user_id": user_id}
    # Honest convention: ``sent`` means the durable delivery exists; the
    # SES outcome and the ``EmailLog`` audit row land from the worker. The
    # ``email_log_id`` key carries the delivery id, same as the recap
    # summary mapping.
    return {
        "status": "sent",
        "user_id": user_id,
        "email_log_id": str(delivery.pk),
    }


def _build_context(user):
    course_db_metadata = (user.import_metadata or {}).get("course_db") or {}
    course_slugs = course_db_metadata.get("course_slugs") or []
    slack_metadata = (user.import_metadata or {}).get("slack") or {}
    return {
        "source_label": user.get_import_source_display(),
        "import_tags": ", ".join(user.tags or []),
        "is_course_db_import": bool(course_slugs),
        "is_slack_import": bool(slack_metadata) or user.import_source == "slack",
        "course_slugs": course_slugs,
        "course_slug_list": ", ".join(course_slugs),
    }
