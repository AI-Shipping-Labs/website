"""Read-only Stripe webhook endpoint verification (issue #1314).

Uses the configured ``STRIPE_SECRET_KEY`` to inspect the Stripe webhook
endpoints in the same mode as the key and prove whether the exact production
callback URL is configured, enabled, in the right mode, non-duplicated, and
subscribed to the five documented events. It NEVER modifies Stripe
configuration and NEVER persists API keys or signing secrets.

Signing-secret health is reported SEPARATELY: Stripe's API cannot reveal or
compare ``whsec_...``, so we only report ``Configured`` (the secret is
non-empty) and ``Verified by delivery`` (a real callback passed signature
verification). We never claim the secret matches from endpoint metadata alone.
"""

import logging

import stripe

from integrations.config import get_config
from payments.models import StripeWebhookDeliveryAttempt, StripeWebhookEndpointCheck
from payments.services.import_stripe import CONFIGURATION_ERRORS
from payments.services.stripe_client import _get_stripe_client

logger = logging.getLogger(__name__)

DEFAULT_EXPECTED_URL = "https://aishippinglabs.com/api/webhooks/payments"

# The exact events this platform documents and handles. Missing either
# cancellation event (or any other) is a failure; extra events are a warning.
REQUIRED_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "customer.updated",
]
CANCELLATION_EVENTS = [
    "customer.subscription.updated",
    "customer.subscription.deleted",
]

Check = StripeWebhookEndpointCheck


def get_expected_webhook_url():
    """The environment-aware expected webhook URL.

    Configurable via ``STRIPE_WEBHOOK_EXPECTED_URL`` so non-production
    environments can point at their own host; defaults to the production URL.
    """
    return (get_config("STRIPE_WEBHOOK_EXPECTED_URL", "") or DEFAULT_EXPECTED_URL).strip()


def _key_mode(secret):
    secret = (secret or "").strip()
    if secret.startswith(("sk_live_", "rk_live_")):
        return Check.KEY_MODE_LIVE
    if secret.startswith(("sk_test_", "rk_test_")):
        return Check.KEY_MODE_TEST
    return Check.KEY_MODE_UNKNOWN


def _ep(endpoint, key, default=None):
    """Read an attribute from a Stripe endpoint (dict or StripeObject)."""
    if isinstance(endpoint, dict):
        return endpoint.get(key, default)
    return getattr(endpoint, key, default)


def signing_secret_evidence():
    """Report signing-secret health without ever inspecting the secret value.

    ``configured`` is whether ``STRIPE_WEBHOOK_SECRET`` is non-empty.
    ``verified_by_delivery`` is whether a real Stripe delivery has passed
    signature verification (any persisted stripe_delivery attempt was recorded
    only AFTER verification succeeded).
    """
    configured = bool((get_config("STRIPE_WEBHOOK_SECRET", "") or "").strip())
    latest = (
        StripeWebhookDeliveryAttempt.objects
        .filter(source=StripeWebhookDeliveryAttempt.SOURCE_STRIPE_DELIVERY)
        .order_by("-received_at")
        .first()
    )
    return {
        "configured": configured,
        "verified_by_delivery": latest is not None,
        "latest_verified_at": latest.received_at if latest is not None else None,
    }


def verify_stripe_endpoint(*, source=Check.SOURCE_STUDIO, checked_by=None):
    """Perform and persist a read-only Stripe webhook endpoint check."""
    expected_url = get_expected_webhook_url()
    secret = (get_config("STRIPE_SECRET_KEY", "") or "").strip()
    key_mode = _key_mode(secret)

    check = Check(
        source=source,
        checked_by=checked_by,
        expected_url=expected_url,
        key_mode=key_mode,
    )

    if not secret:
        check.status = Check.STATUS_FAIL
        check.error_code = "missing_secret_key"
        check.error_message = (
            "STRIPE_SECRET_KEY is not configured, so the endpoint cannot be "
            "verified."
        )
        check.save()
        return check

    try:
        client = _get_stripe_client()
        listing = client.webhook_endpoints.list(params={"limit": 100})
        endpoints = list(_ep(listing, "data", None) or [])
    except (stripe.PermissionError, stripe.AuthenticationError) as exc:
        check.status = Check.STATUS_FAIL
        check.error_code = "permission_denied"
        check.error_message = (
            "The configured Stripe key cannot read webhook endpoints "
            "(restricted-key permission). This is NOT the same as a missing "
            f"endpoint. ({getattr(exc, 'code', '') or 'permission'})"
        )[:500]
        check.save()
        return check
    except (stripe.StripeError, *CONFIGURATION_ERRORS) as exc:
        check.status = Check.STATUS_FAIL
        check.error_code = "stripe_api_error"
        check.error_message = (
            f"Could not read Stripe webhook endpoints: {exc}"
        )[:500]
        check.save()
        return check

    matching = [e for e in endpoints if (_ep(e, "url", "") or "") == expected_url]
    if not matching:
        check.status = Check.STATUS_FAIL
        check.error_code = "endpoint_missing"
        check.error_message = (
            f"No Stripe webhook endpoint targets {expected_url} in "
            f"{key_mode} mode. Create it in the Stripe Dashboard."
        )
        check.save()
        return check

    enabled = [e for e in matching if (_ep(e, "status", "") or "") == "enabled"]
    if not enabled:
        primary = matching[0]
        check.matching_endpoint_id = str(_ep(primary, "id", "") or "")
        check.endpoint_status = str(_ep(primary, "status", "") or "disabled")
        check.status = Check.STATUS_FAIL
        check.error_code = "endpoint_disabled"
        check.error_message = (
            f"The endpoint for {expected_url} exists but is not enabled."
        )
        check.save()
        return check

    primary = enabled[0]
    check.matching_endpoint_id = str(_ep(primary, "id", "") or "")
    check.endpoint_status = str(_ep(primary, "status", "") or "")
    check.api_version = str(_ep(primary, "api_version", "") or "")

    enabled_events = list(_ep(primary, "enabled_events", None) or [])
    check.enabled_events = enabled_events

    subscribes_all = "*" in enabled_events
    missing = (
        [] if subscribes_all
        else [ev for ev in REQUIRED_EVENTS if ev not in enabled_events]
    )
    unexpected = (
        ["*"] if subscribes_all
        else [ev for ev in enabled_events if ev not in REQUIRED_EVENTS]
    )
    check.missing_events = missing
    check.unexpected_events = unexpected

    # Wrong mode: the endpoint's livemode disagrees with the key mode.
    endpoint_livemode = _ep(primary, "livemode", None)
    mode_mismatch = (
        key_mode != Check.KEY_MODE_UNKNOWN
        and isinstance(endpoint_livemode, bool)
        and endpoint_livemode != (key_mode == Check.KEY_MODE_LIVE)
    )
    duplicate = len(enabled) > 1

    if mode_mismatch:
        check.status = Check.STATUS_FAIL
        check.error_code = "wrong_mode"
        check.error_message = (
            f"The endpoint mode does not match the configured {key_mode}-mode "
            "Stripe key."
        )
    elif missing:
        check.status = Check.STATUS_FAIL
        check.error_code = "missing_events"
        check.error_message = (
            "The endpoint is missing required events: " + ", ".join(missing)
        )[:500]
    elif duplicate:
        check.status = Check.STATUS_WARNING
        check.error_code = "duplicate_endpoints"
        check.error_message = (
            f"{len(enabled)} enabled endpoints target {expected_url}; duplicate "
            "deliveries add noise (event idempotency still protects mutations)."
        )
    elif unexpected:
        check.status = Check.STATUS_WARNING
        check.error_code = "unexpected_events"
        check.error_message = (
            "The endpoint enables extra events that are acknowledged and "
            "ignored: " + ", ".join(unexpected)
        )[:500]
    else:
        check.status = Check.STATUS_PASS
        check.error_code = ""
        check.error_message = ""

    check.save()
    return check
