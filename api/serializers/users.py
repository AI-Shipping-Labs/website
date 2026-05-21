"""Serializers for the User Management API (issue #764).

Three pure-function serializers convert ORM rows into the JSON shapes
documented in the OpenAPI spec:

- ``serialize_user_state(user, *, compact=False)``  -- full user state for
  ``GET /api/users/<email>`` and the writes; the ``compact`` flag drops
  ``email_preferences`` / ``tags`` / ``import_metadata`` for the search
  list rows.
- ``serialize_ses_event(event)``  -- one row of the SES history endpoint.
  Intentionally excludes ``raw_payload`` -- operators who need the raw
  SNS body go through the Studio surface in #763.
- ``serialize_email_log(log)``  -- one row of the email-log endpoint.
  Adds a derived ``disposition`` field whose value is the strongest
  observed signal in the order ``sent < delivered < opened < clicked <
  bounced < complained``.

Bounce state (legacy): v1 derives ``bounce_state`` from the existing
``soft_bounce_count`` field plus the ``bounced`` tag. When the structured
``User.bounce_state`` field from issue #766 lands the helper flips to
read it directly -- see the TODO inline.
"""

from django.utils import timezone

from accounts.models import TierOverride
from accounts.utils.display import display_name

BOUNCE_STATE_NONE = "none"
BOUNCE_STATE_SOFT = "soft"
BOUNCE_STATE_PERMANENT = "permanent"

# Disposition strengths -- higher is stronger. The serializer reports the
# strongest non-empty signal so the operator-facing summary surfaces the
# most actionable state in one glance.
_DISPOSITION_SENT = "sent"
_DISPOSITION_DELIVERED = "delivered"
_DISPOSITION_OPENED = "opened"
_DISPOSITION_CLICKED = "clicked"
_DISPOSITION_BOUNCED = "bounced"
_DISPOSITION_COMPLAINED = "complained"


def _isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


def _bounce_state(user):
    """Return one of ``"none" | "soft" | "permanent"`` for ``user``.

    Derivation rules (v1; see issue #766):

    - ``"permanent"`` when the legacy ``bounced`` tag is present.
    - ``"soft"`` when ``soft_bounce_count > 0`` (and no permanent tag).
    - ``"none"`` otherwise.

    TODO(#766): replace tag-derived state with the structured field once
    #766 lands -- the serializer change is a single-line flip from this
    helper to ``user.bounce_state``.
    """
    tags = list(user.tags or [])
    if "bounced" in tags:
        return BOUNCE_STATE_PERMANENT
    if (user.soft_bounce_count or 0) > 0:
        return BOUNCE_STATE_SOFT
    return BOUNCE_STATE_NONE


def serialize_user_state(user, *, compact=False):
    """Serialize a ``User`` row to the API payload.

    The full payload is used by the single-user GET and the writes; the
    list endpoint passes ``compact=True`` to drop the bulky
    ``email_preferences`` / ``tags`` / ``import_metadata`` fields. Email
    preferences and tags are kept on the per-user payload because the
    common operator question after fetching a user is "what tags do they
    carry?" -- bouncing back to a list call would be silly.
    """
    # Tier resolution: the user's base tier slug + level. ``user.tier_id``
    # can legitimately be NULL for the bare "free" case.
    if user.tier_id:
        tier_payload = {
            "slug": user.tier.slug,
            "level": user.tier.level,
        }
    else:
        tier_payload = {"slug": "free", "level": 0}

    # Override resolution -- callers care about the BOOLEAN ("is there an
    # active override?"). The override object itself is intentionally not
    # exposed here (separate Studio surface owns the audit trail).
    # ``content.access.get_active_override`` is the canonical helper but
    # lives in the ``content`` app; replicating the predicate here keeps
    # the API serializer free of that dependency.
    tier_override_active = TierOverride.objects.filter(
        user=user,
        is_active=True,
        expires_at__gt=timezone.now(),
    ).exists()

    payload = {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "display_name": display_name(user),
        "tier": tier_payload,
        "tier_override_active": tier_override_active,
        "unsubscribed": bool(user.unsubscribed),
        "soft_bounce_count": int(user.soft_bounce_count or 0),
        "bounce_state": _bounce_state(user),
        "email_verified": bool(user.email_verified),
        "slack_member": bool(user.slack_member),
        "slack_user_id": user.slack_user_id or "",
        "stripe_customer_id": user.stripe_customer_id or "",
        "subscription_id": user.subscription_id or "",
        "date_joined": _isoformat_or_none(user.date_joined),
        "last_login": _isoformat_or_none(user.last_login),
    }
    if not compact:
        payload["tags"] = list(user.tags or [])
        payload["email_preferences"] = dict(user.email_preferences or {})
        payload["import_metadata"] = dict(user.import_metadata or {})
    return payload


def serialize_ses_event(event):
    """Serialize one ``SesEvent`` row for the API.

    Excludes ``raw_payload`` deliberately -- the API summary surface is
    the operator-readable digest; the raw SNS body lives in the Studio
    surface (#763) for the deep-dive.
    """
    return {
        "message_id": event.message_id,
        "event_type": event.event_type,
        "received_at": _isoformat_or_none(event.received_at),
        "recipient_email": event.recipient_email or "",
        "bounce_type": event.bounce_type or "",
        "bounce_subtype": event.bounce_subtype or "",
        "diagnostic_code": event.diagnostic_code or "",
        "action_taken": event.action_taken or "",
        "email_log_id": event.email_log_id,
    }


def _email_log_disposition(log):
    """Return the strongest observable signal on an ``EmailLog`` row.

    Ladder (weakest -> strongest):
    ``sent -> delivered -> opened -> clicked -> bounced -> complained``.

    Note: we do NOT track a separate "delivered" timestamp on EmailLog
    today; ``opened`` (or stronger) implies delivery, so the ladder
    collapses to whichever of the recorded fields is non-null.
    """
    if log.complained_at is not None:
        return _DISPOSITION_COMPLAINED
    if log.bounced_at is not None:
        return _DISPOSITION_BOUNCED
    if log.clicked_at is not None or (log.clicks or 0) > 0:
        return _DISPOSITION_CLICKED
    if log.opened_at is not None or (log.opens or 0) > 0:
        return _DISPOSITION_OPENED
    return _DISPOSITION_SENT


def serialize_email_log(log):
    """Serialize one ``EmailLog`` row for the API."""
    return {
        "id": log.id,
        "email_type": log.email_type,
        "sent_at": _isoformat_or_none(log.sent_at),
        "ses_message_id": log.ses_message_id or "",
        "opened_at": _isoformat_or_none(log.opened_at),
        "opens": int(log.opens or 0),
        "clicked_at": _isoformat_or_none(log.clicked_at),
        "clicks": int(log.clicks or 0),
        "bounced_at": _isoformat_or_none(log.bounced_at),
        "bounce_type": log.bounce_type or "",
        "bounce_subtype": log.bounce_subtype or "",
        "complained_at": _isoformat_or_none(log.complained_at),
        "campaign_id": log.campaign_id,
        "disposition": _email_log_disposition(log),
    }
