"""Free-member welcome email sender."""

import logging

from email_app.models import EmailLog
from email_app.services.email_service import EmailService, EmailServiceError

FREE_WELCOME_EMAIL_TYPE = "free_welcome"

logger = logging.getLogger(__name__)


def send_free_welcome_email(user):
    """Send the Free welcome email at most once per user.

    Returns the existing or newly-created ``EmailLog`` row. If the send fails,
    logs the expected email-service error and returns ``None`` so signup and
    verification flows never fail because of welcome delivery.
    """
    if user is None or not getattr(user, "pk", None):
        return None

    existing = (
        EmailLog.objects
        .filter(user=user, email_type=FREE_WELCOME_EMAIL_TYPE)
        .order_by("sent_at")
        .first()
    )
    if existing is not None:
        return existing

    try:
        return EmailService().send(user, FREE_WELCOME_EMAIL_TYPE)
    except EmailServiceError:
        logger.exception(
            "Failed to send Free welcome email to %s (user_id=%s)",
            user.email,
            user.pk,
        )
        return None
