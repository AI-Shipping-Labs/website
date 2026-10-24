"""Member notification mail for onboarding completion (#2006)."""

import logging

from community_base.jobs.runner import PermanentJobError

from accounts.services.onboarding_booking import get_founder_booking_url
from email_app.hooks import _member_greeting
from email_app.package_mail import send_package_mail
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def send_onboarding_submitted_mail(response) -> None:
    """Send transactional confirmation email to the member on first onboarding submission.

    AC3: Uses send_package_mail with idempotency key onboarding_submitted:<response_id>.
    Durable context must not contain rendered URLs (#1613 guard);
    the worker resolver mints the booking link at delivery time.
    Failure to send is logged and swallowed so it never breaks submission.
    """
    try:
        user = getattr(response, "respondent", None)
        if user is None:
            return
        if not getattr(user, "email", ""):
            return

        idempotency_key = f"onboarding_submitted:{response.id}"
        send_package_mail(
            user=user,
            template_name="onboarding_submitted",
            context={},
            idempotency_key=idempotency_key,
            related=response,
        )
    except Exception:
        logger.exception(
            "Failed to send onboarding submission email for response %s",
            getattr(response, "id", None),
        )


def resolve_onboarding_submitted_context(delivery, context) -> None:
    """Resolve greeting and absolute founder booking link at delivery time (#1613)."""
    user = delivery.recipient_user
    if user is None:
        raise PermanentJobError("onboarding_submitted_user_missing")
    if not getattr(user, "pk", None):
        raise PermanentJobError("onboarding_submitted_user_missing")

    if delivery.related_object_type == "questionnaires.response":
        from questionnaires.models import Response  # noqa: PLC0415

        rel_id = delivery.related_object_id
        if not Response.objects.filter(pk=rel_id).exists():
            raise PermanentJobError("onboarding_submitted_response_missing")

    _member_greeting(delivery, context)
    base_url = site_base_url().rstrip("/")
    context["site_url"] = base_url
    context["booking_url"] = get_founder_booking_url("alexey", absolute=True)
