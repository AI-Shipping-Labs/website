"""Relay transport boundary for the remaining rendered campaign producers."""

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from email_app.services.email_errors import (
    EmailServiceError,
    EmailTransportOutcomeUnknown,
)
from email_app.services.relay_campaigns import (
    RelayCampaignDeliveryAmbiguous,
    RelayCampaignDeliveryRejected,
    get_relay_api_key,
    get_relay_base_url,
    send_campaign_delivery_to_relay,
)


def send_rendered_relay(
    to_email, subject, html_body, *, text_body, email_type=None,
    unsubscribe_url=None, cc=None, bcc=None, redact_recipient=False,
    idempotency_key=None, metadata=None,
):
    """Return a Relay message id; only offline tests may skip configuration."""
    if not get_relay_base_url() or not get_relay_api_key():
        if settings.TESTING:
            return "relay-not-configured-noop"
        raise ImproperlyConfigured("RELAY_BASE_URL and RELAY_API_KEY must be configured")
    try:
        return send_campaign_delivery_to_relay(
            recipient_email=to_email, subject=subject, html_body=html_body,
            text_body=text_body, idempotency_key=idempotency_key or f"relay-{uuid4().hex}",
            unsubscribe_url=unsubscribe_url, metadata=metadata,
        )
    except RelayCampaignDeliveryRejected as exc:
        raise EmailServiceError("Relay rejected the email") from exc
    except RelayCampaignDeliveryAmbiguous as exc:
        raise EmailTransportOutcomeUnknown("Relay send outcome is unknown") from exc
