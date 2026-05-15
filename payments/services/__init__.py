"""Business logic for Stripe payments and subscription lifecycle.

The active product model is Stripe Payment Links for checkout, Stripe
webhooks for fulfillment, and Customer Portal for billing management.

This package was originally a single ``__init__.py`` that grew to ~1000
lines mixing seven unrelated concerns. It is now split into focused
modules under this package, with ``__init__.py`` acting as a thin
re-export shim so every existing import path keeps working:

- ``stripe_client`` — low-level Stripe wrappers and subscription helpers.
- ``signatures`` — webhook signature verification and event idempotency.
- ``community_hooks`` — community invite / remove / reactivate enqueue.
- ``conversion_attribution`` — UTM snapshot at conversion time.
- ``subscriptions`` — subscription queries and deprecated mutators.
- ``webhook_handlers`` — Stripe event handlers (the public ``handle_*``).
- ``checkout_deprecated`` — hard-deprecated Checkout Session creation.

``stripe``, ``send_mail``, ``get_config``, ``logger``, and
``WebhookPermanentError`` are imported here so tests can keep patching
them at ``payments.services.<name>``; implementation modules look these
up through this package so the patches actually take effect.
"""

import logging

import stripe
from django.core.mail import send_mail

from integrations.config import get_config
from payments.exceptions import WebhookPermanentError

logger = logging.getLogger(__name__)

# Re-exports — every line below uses plain ``from .module import name``
# so the imported callable is bound on the package namespace with
# identity preserved. Tests that ``mock.patch("payments.services.X")``
# replace the attribute here, and implementation modules read it back
# via ``from payments import services as _services`` for the patches
# to take effect at call time.
from .checkout_deprecated import create_checkout_session  # noqa: E402
from .community_hooks import (  # noqa: E402
    _community_invite,
    _community_reactivate,
    _community_remove,
    _community_schedule_removal,
)
from .conversion_attribution import _record_conversion_attribution  # noqa: E402
from .signatures import (  # noqa: E402
    is_event_already_processed,
    record_processed_event,
    verify_webhook_signature,
)
from .stripe_client import (  # noqa: E402
    _first_subscription_item,
    _get_stripe_client,
    _stripe_value,
    _subscription_period_end,
    _subscription_price_id,
    _tier_for_price_id,
)
from .subscriptions import (  # noqa: E402
    _get_subscription_period_end,
    _get_subscription_price_id,
    _tier_from_subscription,
    cancel_subscription,
    downgrade_subscription,
    upgrade_subscription,
)
from .webhook_handlers import (  # noqa: E402
    _handle_course_purchase,
    _send_payment_notification_email,
    handle_checkout_completed,
    handle_customer_updated,
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
)

__all__ = [
    # Re-exported exception
    "WebhookPermanentError",
    # Webhook handlers (public)
    "handle_checkout_completed",
    "handle_customer_updated",
    "handle_subscription_updated",
    "handle_subscription_deleted",
    "handle_invoice_payment_failed",
    # Webhook signature + idempotency
    "verify_webhook_signature",
    "is_event_already_processed",
    "record_processed_event",
    # Deprecated direct mutators (raise RuntimeError)
    "create_checkout_session",
    "upgrade_subscription",
    "downgrade_subscription",
    "cancel_subscription",
]
