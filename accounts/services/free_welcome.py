"""Free-member welcome email sender."""

import logging

from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.db import IntegrityError

from email_app.models import EmailLog
from email_app.package_mail import send_package_mail

FREE_WELCOME_EMAIL_TYPE = "free_welcome"

logger = logging.getLogger(__name__)


def send_free_welcome_email(user):
    """Send the Free welcome email at most once per member.

    Returns the durable ``EmailDelivery`` — the existing row when one is
    already in flight or accepted under the ``free_welcome:{user.pk}``
    idempotency key — or ``None`` when the member row is unsaved, the
    member was already welcomed on the pre-package EmailService path
    (that legacy ``EmailLog`` has no delivery behind it), or the send was
    refused locally. Never raises into signup, verification or OAuth
    flows.
    """
    if user is None or not getattr(user, "pk", None):
        return None

    idempotency_key = f"free_welcome:{user.pk}"
    existing = EmailDelivery.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing
    if (
        EmailLog.objects
        .filter(user=user, email_type=FREE_WELCOME_EMAIL_TYPE)
        .exists()
    ):
        return None

    try:
        return send_package_mail(
            user,
            FREE_WELCOME_EMAIL_TYPE,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        # Lost the create race on the unique idempotency key; the winning
        # transaction's delivery is the same logical send.
        logger.info("Free welcome email already in flight (user_id=%s)", user.pk)
        return EmailDelivery.objects.filter(idempotency_key=idempotency_key).first()
    except MailError:
        logger.exception(
            "Failed to send Free welcome email to %s (user_id=%s)",
            user.email,
            user.pk,
        )
        return None
