"""Relay contact and subscription sync for AISL (plan issue A6.2).

The user rows stay authoritative until A6.3 (decision D4); this module
mirrors their contact state into Relay through the package contacts
client. Preference and unsubscribe changes dispatch registered job
handlers after commit, and Relay callbacks project back onto the user
through the ``relay_callback_processed`` signal.
``MAIL_PREFERENCE_RESOLVER`` keeps reading the user unchanged.

The AISL scope on Relay is audience ``aisl`` with client slug
``aisl-website`` -- the pairing fixed by the R6.3 campaign-parity work
and the package client contract fixtures.
"""

from __future__ import annotations

import logging
import uuid

from community_base.jobs import dispatch_after_commit, register_handler
from community_base.jobs.runner import PermanentJobError, RetryableJobError
from community_base.mail.relay_contacts import (
    RelayContactsError,
    configured_contacts_client,
)
from community_base.mail.signals import relay_callback_processed
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.dispatch import receiver

from accounts.utils.bounce import mark_permanent_bounce, record_soft_bounce

logger = logging.getLogger(__name__)

User = get_user_model()

RELAY_AUDIENCE = "aisl"
RELAY_CLIENT = "aisl-website"

# Relay delivers the double opt-in message from this client-owned template;
# importing the template into the development Relay tenant is A6.1 work.
DOUBLE_OPT_IN_TEMPLATE_KEY = "newsletter-double-opt-in"
DOUBLE_OPT_IN_CATEGORY = "newsletter"

# Suppression reason codes (Relay callback contract version 1) that mean the
# contact opted out; the codes are safe enumerations, never recipient data.
UNSUBSCRIBE_SUPPRESSION_CODES = frozenset(
    {"global_unsubscribe", "client_unsubscribe", "audience_unsubscribe"}
)


def contacts_client():
    """The configured Relay contacts client for this site.

    Reads ``RELAY_BASE_URL``/``RELAY_API_KEY`` through the package. Tests
    patch this with a client whose transport is a FakeRelay.
    """
    return configured_contacts_client(RELAY_CLIENT)


def contact_tags(user):
    """Relay tags for a user: the tier tag plus the operator contact tags."""
    tags = []
    if user.tier_id and user.tier.slug:
        tags.append(f"tier:{user.tier.slug}")
    tags.extend(sorted(user.contact_tags.values_list("slug", flat=True)))
    return tags


def sync_user_to_relay(user, client):
    """Upsert one user's contact state; returns the parsed Relay contact.

    The local ``unsubscribed`` flag is the newsletter decision until A6.3,
    so it drives the client-scoped subscription status, and the site's
    email verification state rides along as the contact verification flag.
    """
    return client.upsert_contact(
        user.email,
        RELAY_AUDIENCE,
        tags=contact_tags(user),
        status="unsubscribed" if user.unsubscribed else "subscribed",
        verified=bool(user.email_verified),
    )


def _job_error(error):
    """Map a RelayContactsError onto the durable job error classes."""
    if error.retryable or error.ambiguous:
        return RetryableJobError(error.code)
    return PermanentJobError(error.code)


def _user_from_payload(payload):
    raw_id = payload.get("user_id") if isinstance(payload, dict) else None
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise PermanentJobError("invalid_relay_sync_payload")
    return User.objects.filter(pk=raw_id).first()


def dispatch_contact_sync(user_id):
    """Mirror one user's contact state to Relay after commit."""
    dispatch_after_commit(
        "email_app.relay_sync.sync_contact",
        key=f"email-app-relay-sync-{user_id}-{uuid.uuid4().hex}",
        payload={"user_id": user_id},
    )


def dispatch_unsubscribe(user_id):
    """Unsubscribe the contact from the whole AISL audience after commit."""
    dispatch_after_commit(
        "email_app.relay_sync.unsubscribe_contact",
        key=f"email-app-relay-unsubscribe-{user_id}-{uuid.uuid4().hex}",
        payload={"user_id": user_id},
    )


def dispatch_request_verification(user_id):
    """Ask Relay to deliver the double opt-in message after commit."""
    dispatch_after_commit(
        "email_app.relay_sync.request_contact_verification",
        key=f"email-app-relay-verify-{user_id}-{uuid.uuid4().hex}",
        payload={"user_id": user_id},
    )


@register_handler("email_app.relay_sync.sync_contact")
def sync_contact(context, payload):
    del context
    user = _user_from_payload(payload)
    if user is None:
        return
    try:
        sync_user_to_relay(user, contacts_client())
    except RelayContactsError as error:
        raise _job_error(error) from error


@register_handler("email_app.relay_sync.unsubscribe_contact")
def unsubscribe_contact(context, payload):
    del context
    user = _user_from_payload(payload)
    if user is None:
        return
    try:
        contacts_client().unsubscribe(
            user.email, RELAY_AUDIENCE, scope="audience", reason="member request"
        )
    except RelayContactsError as error:
        raise _job_error(error) from error


@register_handler("email_app.relay_sync.request_contact_verification")
def request_contact_verification(context, payload):
    del context
    user = _user_from_payload(payload)
    if user is None or user.email_verified:
        return
    try:
        contacts_client().request_verification(
            user.email,
            RELAY_AUDIENCE,
            category=DOUBLE_OPT_IN_CATEGORY,
            template_key=DOUBLE_OPT_IN_TEMPLATE_KEY,
        )
    except RelayContactsError as error:
        raise _job_error(error) from error


def confirm_subscription(token):
    """Exchange a Relay double opt-in token and mirror the verified state.

    Returns ``(outcome, user_id)``. The outcome is ``"confirmed"``,
    ``"invalid"`` (malformed, expired, tampered or unknown token),
    ``"unknown_user"`` (valid token, but the never-verified signup was
    already purged per issue #452) or ``"unavailable"`` (Relay could not
    be reached; the token stays valid so the link can be retried).

    The token is exchanged in the request instead of a durable job: it is
    a bearer secret from a confirm URL, and durable job payloads must not
    retain bearer tokens. The local mirror write runs after the exchange
    in its own short transaction.
    """
    try:
        client = contacts_client()
    except ImproperlyConfigured:
        logger.warning("relay_contacts_client_not_configured")
        return "unavailable", None
    try:
        confirmation = client.confirm_verification(token)
    except RelayContactsError as error:
        if error.fields.get("token") == "invalid" or error.status == 404:
            return "invalid", None
        logger.warning("relay_confirm_unavailable code=%s", error.code)
        return "unavailable", None
    user = User.objects.filter(email__iexact=confirmation.email).first()
    if user is None:
        return "unknown_user", None
    preferences = dict(user.email_preferences or {})
    preferences["newsletter"] = True
    user.email_preferences = preferences
    user.unsubscribed = False
    user.email_verified = True
    # A confirmed address is never auto-purged: the issue #452 window only
    # applies to accounts that never confirmed, matching verify_email_api.
    user.verification_expires_at = None
    user.save(
        update_fields=[
            "email_preferences",
            "unsubscribed",
            "email_verified",
            "verification_expires_at",
        ]
    )
    with transaction.atomic():
        dispatch_contact_sync(user.pk)
    return "confirmed", user.pk


@receiver(relay_callback_processed, dispatch_uid="email_app.relay_sync.on_relay_callback")
def on_relay_callback(sender, *, event_type, reason_code, delivery, created, **kwargs):
    """Project Relay contact events back onto the user (A6.2 step 3).

    Event-to-field mapping (Relay callback contract version 1):

    - ``delivery.bounced`` ``hard_bounce``: permanent bounce; the site
      helper also sets ``unsubscribed`` and stores the diagnostic.
    - ``delivery.bounced`` ``soft_bounce``: soft bounce; the site counter
      increments and the threshold flip stays site-owned.
    - ``delivery.suppressed`` ``global_unsubscribe``,
      ``client_unsubscribe`` or ``audience_unsubscribe``: the user counts
      as unsubscribed, mirroring the published ``newsletter`` preference
      the same way the unsubscribe endpoint does.
    - ``delivery.suppressed`` ``hard_bounce``: permanent bounce.
    - ``subscription.changed``: recorded only. Contract version 1 carries
      no contact identity on contact-level transitions, so the user
      cannot be correlated from the callback; this gap is reported in the
      A6.2 pull request rather than papered over (real-Relay conformance
      is R6.1's).

    State changes run only for ``created`` callbacks: the package
    deduplicates event ids and re-emits replays with ``created=False``, so
    guarding here keeps the soft-bounce counter idempotent.
    """
    del sender
    if not created or delivery is None or delivery.recipient_user_id is None:
        return
    user = delivery.recipient_user
    diagnostic = f"relay {event_type} {reason_code}".strip()
    if event_type == "delivery.bounced":
        if reason_code == "hard_bounce":
            mark_permanent_bounce(user, diagnostic=diagnostic)
        elif reason_code == "soft_bounce":
            record_soft_bounce(user, diagnostic=diagnostic)
    elif event_type == "delivery.suppressed":
        if reason_code in UNSUBSCRIBE_SUPPRESSION_CODES and not user.unsubscribed:
            preferences = dict(user.email_preferences or {})
            preferences["newsletter"] = False
            user.email_preferences = preferences
            user.unsubscribed = True
            user.save(update_fields=["unsubscribed", "email_preferences"])
        elif reason_code == "hard_bounce":
            mark_permanent_bounce(user, diagnostic=diagnostic)
