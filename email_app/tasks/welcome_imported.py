"""Background tasks for imported-user welcome emails."""

import datetime

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model

from email_app.models import EmailLog
from email_app.services.email_service import EmailService

JWT_ALGORITHM = "HS256"


def enqueue_imported_welcome_email(user_id):
    """Django-Q scheduled entry point: enqueue the actual send task."""
    from jobs.tasks import async_task

    return async_task(
        "email_app.tasks.welcome_imported.send_imported_welcome_email",
        user_id,
    )


def send_imported_welcome_email(user_id):
    """Send the imported-user welcome email once per user."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {"status": "skipped", "reason": "missing_user", "user_id": user_id}

    if user.unsubscribed:
        return {"status": "skipped", "reason": "unsubscribed", "user_id": user_id}

    if EmailLog.objects.filter(user=user, email_type="welcome_imported").exists():
        return {"status": "skipped", "reason": "already_sent", "user_id": user_id}

    service = EmailService()
    email_log = service.send(user, "welcome_imported", _build_context(user))
    if email_log is None:
        return {"status": "skipped", "reason": "unsubscribed", "user_id": user_id}
    return {"status": "sent", "user_id": user_id, "email_log_id": email_log.pk}


def _build_context(user):
    site_url = settings.SITE_BASE_URL.rstrip("/")
    return {
        "source_label": user.get_import_source_display(),
        "import_tags": ", ".join(user.tags or []),
        "password_reset_url": _build_password_reset_url(user),
        "sign_in_url": f"{site_url}/login/",
    }


def _build_password_reset_url(user):
    site_url = settings.SITE_BASE_URL.rstrip("/")
    payload = {
        "user_id": user.pk,
        "action": "password_reset",
        "exp": (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1)
        ),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
    return f"{site_url}/api/password-reset?token={token}"
