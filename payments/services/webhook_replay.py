"""Guarded event-ID inspection and replay for cancellation callbacks.

Issue #1314. Studio can INSPECT a Stripe event (read-only preview); the API
can additionally REPLAY it under strict guards. Both paths retrieve the event
from Stripe, require a same-mode ``customer.subscription.updated`` /
``customer.subscription.deleted`` event, and resolve exactly one safe target.

A replay write requires ``dry_run=false`` and the exact confirmation token; it
reuses the real handler transaction (via
:func:`payments.services.webhook_dispatch.process_event`) rather than a second
cancellation implementation, and is idempotent on repeat.
"""

import logging

import stripe

from integrations.config import get_config
from payments.exceptions import (
    WebhookAmbiguousUserError,
    WebhookUnmatchedUserError,
)
from payments.models import (
    StripeWebhookDeliveryAttempt,
    WebhookEvent,
)
from payments.services.import_stripe import CONFIGURATION_ERRORS
from payments.services.stripe_client import _get_stripe_client
from payments.services.subscription_resolution import resolve_subscription_user
from payments.services.webhook_dispatch import (
    CANCELLATION_EVENT_TYPES,
    process_event,
    safe_object_ids,
)

logger = logging.getLogger(__name__)

CONFIRM_TOKEN = "replay_cancellation_event"  # noqa: S105 - not a secret


class ReplayError(Exception):
    """A safe, non-secret rejection with a stable machine code."""

    def __init__(self, code, message, *, http_status=422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _val(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _key_live():
    secret = (get_config("STRIPE_SECRET_KEY", "") or "").strip()
    if secret.startswith(("sk_live_", "rk_live_")):
        return True
    if secret.startswith(("sk_test_", "rk_test_")):
        return False
    return None


def _retrieve_event(event_id):
    try:
        client = _get_stripe_client()
        return client.events.retrieve(event_id)
    except stripe.InvalidRequestError as exc:
        if getattr(exc, "code", None) == "resource_missing" or getattr(
            exc, "http_status", None
        ) == 404:
            raise ReplayError(
                "event_not_found",
                f"Stripe has no event with id {event_id}.",
                http_status=404,
            ) from exc
        raise ReplayError(
            "stripe_api_error", f"Stripe rejected the event lookup: {exc}",
        ) from exc
    except (stripe.PermissionError, stripe.AuthenticationError) as exc:
        raise ReplayError(
            "permission_denied",
            "The configured Stripe key cannot read events.",
        ) from exc
    except (stripe.StripeError, *CONFIGURATION_ERRORS) as exc:
        raise ReplayError(
            "stripe_api_error", f"Could not retrieve the event: {exc}",
        ) from exc


def _event_object(event):
    data = _val(event, "data", {}) or {}
    obj = _val(data, "object", {}) or {}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return obj
    return dict(obj)


def _local_history(event_id):
    terminal = WebhookEvent.objects.filter(stripe_event_id=event_id).first()
    attempts = list(
        StripeWebhookDeliveryAttempt.objects
        .filter(stripe_event_id=event_id)
        .order_by("received_at")
    )
    return {
        "terminal_status": terminal.status if terminal else None,
        "attempts": [
            {
                "attempt_number": a.attempt_number,
                "outcome": a.outcome,
                "http_status": a.http_status,
                "source": a.source,
                "received_at": a.received_at.isoformat() if a.received_at else None,
            }
            for a in attempts
        ],
    }


def _tier_slug(tier):
    return tier.slug if tier is not None else "free"


def _member_state(user):
    return {
        "user_id": user.pk,
        "email": user.email,
        "tier": _tier_slug(user.tier),
        "pending_tier": _tier_slug(user.pending_tier) if user.pending_tier else None,
        "subscription_id": user.subscription_id or "",
    }


def _future_cancel(obj):
    from payments.services.webhook_handlers import _future_cancel_at
    return _future_cancel_at(obj.get("cancel_at")) is not None


def _proposed_transition(event_type, obj, user):
    current = _member_state(user)
    if event_type == "customer.subscription.deleted":
        proposed = {
            "tier": "free",
            "pending_tier": None,
            "subscription_id": "",
        }
    else:  # customer.subscription.updated
        scheduling_cancel = bool(obj.get("cancel_at_period_end")) or _future_cancel(obj)
        proposed = {
            "tier": current["tier"],
            "pending_tier": "free" if scheduling_cancel else None,
            "subscription_id": _val(obj, "id", "") or current["subscription_id"],
        }
    return {"current": current, "proposed": proposed}


def preview_event(event_id):
    """Read-only preview. Never writes. Raises :class:`ReplayError` on reject.

    Returns a dict describing event type/mode, local history, the exact target
    or resolution failure, and the membership transition a confirmed replay
    would perform.
    """
    event = _retrieve_event(event_id)
    event_type = _val(event, "type", "") or ""
    livemode = _val(event, "livemode", None)
    obj = _event_object(event)
    ids = safe_object_ids(event_type, obj)

    history = _local_history(event_id)

    result = {
        "event_id": event_id,
        "event_type": event_type,
        "livemode": livemode,
        "stripe_customer_id": ids["customer_id"],
        "stripe_subscription_id": ids["subscription_id"],
        "local_history": history,
        "target": None,
        "resolution": None,
        "transition": None,
    }

    if event_type not in CANCELLATION_EVENT_TYPES:
        result["resolution"] = "unsupported_event_type"
        return result

    key_live = _key_live()
    if key_live is not None and isinstance(livemode, bool) and livemode != key_live:
        result["resolution"] = "mode_mismatch"
        return result

    if history["terminal_status"] == WebhookEvent.STATUS_PROCESSED:
        result["resolution"] = "already_processed"

    try:
        resolution = resolve_subscription_user(
            ids["subscription_id"], ids["customer_id"],
        )
    except WebhookAmbiguousUserError as exc:
        result["resolution"] = result["resolution"] or "ambiguous_user"
        result["target_error"] = str(exc)
        return result
    except WebhookUnmatchedUserError:
        result["resolution"] = result["resolution"] or "unmatched_user"
        return result

    if resolution.stale_customer_user is not None:
        result["resolution"] = result["resolution"] or "stale_subscription"
        result["target"] = _member_state(resolution.stale_customer_user)
        return result

    user = resolution.user
    result["target"] = _member_state(user)
    result["transition"] = _proposed_transition(event_type, obj, user)
    if result["resolution"] is None:
        result["resolution"] = "replayable"
    return result


def replay_event(event_id, *, requested_by, dry_run=True, confirm=""):
    """Inspect (dry-run) or execute a guarded cancellation replay.

    Dry-run returns the same preview as :func:`preview_event`. A write requires
    ``dry_run=False`` and ``confirm == 'replay_cancellation_event'``.
    """
    preview = preview_event(event_id)

    resolution = preview["resolution"]
    if resolution == "unsupported_event_type":
        raise ReplayError(
            "unsupported_event_type",
            "Only same-mode customer.subscription.updated/deleted events can "
            "be replayed.",
        )
    if resolution == "mode_mismatch":
        raise ReplayError(
            "mode_mismatch",
            "The event mode does not match the configured Stripe key mode.",
        )

    if dry_run:
        preview["dry_run"] = True
        return preview

    if confirm != CONFIRM_TOKEN:
        raise ReplayError(
            "confirmation_required",
            "A replay write requires confirm='replay_cancellation_event'.",
        )

    if resolution == "already_processed":
        raise ReplayError(
            "already_processed",
            "This event already has a processed terminal record; replay is a "
            "no-op.",
        )
    if resolution == "ambiguous_user":
        raise ReplayError(
            "ambiguous_user",
            "The event resolves to more than one local user; replay cannot "
            "safely pick an owner.",
        )
    if resolution == "unmatched_user":
        raise ReplayError(
            "unmatched_user",
            "The event resolves to no local user.",
        )
    if resolution == "stale_subscription":
        raise ReplayError(
            "stale_subscription",
            "The event is for an older subscription than the customer's "
            "current authoritative one.",
        )
    if resolution != "replayable":
        raise ReplayError(
            "not_replayable",
            f"The event is not in a replayable state ({resolution}).",
        )

    # Re-retrieve the event object for dispatch (fresh, same-mode).
    event = _retrieve_event(event_id)
    event_type = _val(event, "type", "") or ""
    livemode = _val(event, "livemode", None)
    obj = _event_object(event)

    target = preview["target"]
    from accounts.models import User
    before = _member_state(User.objects.get(pk=target["user_id"]))

    outcome, http_status = process_event(
        event_id=event_id,
        event_type=event_type,
        obj=obj,
        livemode=livemode,
        source=StripeWebhookDeliveryAttempt.SOURCE_OPERATOR_REPLAY,
        requested_by=requested_by,
    )

    after = _member_state(User.objects.get(pk=target["user_id"]))
    return {
        "event_id": event_id,
        "event_type": event_type,
        "dry_run": False,
        "outcome": outcome,
        "membership_before": before,
        "membership_after": after,
        "local_history": _local_history(event_id),
    }
