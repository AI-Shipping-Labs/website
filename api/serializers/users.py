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
"""

from django.utils import timezone

from accounts.lifecycle import lifecycle_payload
from accounts.models import TierOverride
from accounts.services.subscription_summary import subscription_summary
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

    Reads the structured ``User.bounce_state`` field (issue #766) directly.
    The SES webhook handler and ``process_missed_bounces`` are the only
    writers; both set this field on permanent and soft bounces.

    The module-level ``BOUNCE_STATE_*`` constants pin the wire-format
    strings the API contract promises. They happen to match the model's
    ``TextChoices`` values today but are kept independent of that
    implementation detail so a model rename never silently breaks the API.

    Empty / NULL falls back to ``"none"`` -- defensive only; the field has
    a non-null default at the schema layer. We deliberately do NOT fall
    back to the legacy ``"bounced"`` tag: a missing ``bounce_state`` on a
    bouncy row is a backfill bug we want loud, not papered over.
    """
    return user.bounce_state or BOUNCE_STATE_NONE


def serialize_user_state(user, *, compact=False):
    """Serialize a ``User`` row to the API payload.

    The full payload is used by the single-user GET and the writes; the
    list endpoint passes ``compact=True`` to drop the bulky
    ``email_preferences`` / ``tags`` / ``import_metadata`` fields. Email
    preferences and tags are kept on the per-user payload because the
    common operator question after fetching a user is "what tags do they
    carry?" -- bouncing back to a list call would be silly.
    """
    # Base tier resolution: the user's actually-paid tier slug + level.
    # ``user.tier_id`` can legitimately be NULL for the bare "free" case.
    if user.tier_id:
        base_tier_payload = {
            "slug": user.tier.slug,
            "level": user.tier.level,
        }
    else:
        base_tier_payload = {"slug": "free", "level": 0}

    # Override resolution -- callers care about the BOOLEAN ("is there an
    # active override?") and (full payload only) the override SUMMARY
    # object. The model guarantees one active override per user, so the
    # newest active non-expired row is THE override. We fetch that single
    # row once and derive ``tier_override_active``, the ``tier_override``
    # object, AND the effective tier from it -- no second query, no drift.
    # ``content.access.get_active_override`` is the canonical helper but
    # lives in the ``content`` app; replicating the predicate here keeps
    # the API serializer free of that dependency.
    active_override = (
        TierOverride.objects.filter(
            user=user,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .select_related("override_tier", "granted_by")
        .order_by("-created_at")
        .first()
    )
    tier_override_active = active_override is not None

    # Effective tier = ``max(base, override)`` by level (issue #965). An
    # override only ever RAISES the reported tier, never lowers it, mirroring
    # ``content.access.get_user_level``. We deliberately do NOT call
    # ``get_user_level`` here: it short-circuits staff/superuser to
    # ``LEVEL_PREMIUM``, which would wrongly report every admin as Premium in
    # the API. This serializer reports the member's actual subscription /
    # override tier, not staff escalation, so we compute the max directly.
    base_level = base_tier_payload["level"]
    if (
        active_override is not None
        and active_override.override_tier.level > base_level
    ):
        tier_payload = {
            "slug": active_override.override_tier.slug,
            "level": active_override.override_tier.level,
            "source": "override",
        }
    else:
        tier_payload = {
            "slug": base_tier_payload["slug"],
            "level": base_tier_payload["level"],
            "source": "subscription" if base_level > 0 else "free",
        }

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
        **lifecycle_payload(user),
    }
    if not compact:
        payload["tags"] = list(user.tags or [])
        payload["email_preferences"] = dict(user.email_preferences or {})
        payload["import_metadata"] = dict(user.import_metadata or {})
        # ``base_tier`` (issue #965): the actually-paid tier — the old meaning
        # of ``tier`` before the effective-tier change. Additive, so callers
        # that genuinely need the paid tier can read it without re-deriving
        # from ``tier_override``. Full payload only, like ``tier_override``.
        payload["base_tier"] = base_tier_payload
        payload["tier_override"] = _serialize_tier_override(active_override)
        # Email aliases (issue #840a): the normalized alias emails routing to
        # this account, so operators see Stripe-routing at a glance. Read-only
        # addition mirroring how ``tier_override`` was added (#834).
        payload["aliases"] = list(
            user.email_aliases.order_by("email").values_list("email", flat=True)
        )
        payload["subscription"] = subscription_summary(user)
    return payload


def _serialize_tier_override(override):
    """Serialize the active ``TierOverride`` summary, or ``None``.

    Surfaces only the operator-facing summary fields (``tier_slug``,
    ``level``, ``expires_at``, ``granted_by``) -- the full audit trail
    (``original_tier``, ``created_at``, revocations) stays in Studio.
    ``granted_by`` is the granter's email string, or ``None`` when the
    granting admin was deleted (``SET_NULL`` FK).
    """
    if override is None:
        return None
    return {
        "tier_slug": override.override_tier.slug,
        "level": override.override_tier.level,
        "expires_at": _isoformat_or_none(override.expires_at),
        "granted_by": override.granted_by.email if override.granted_by else None,
    }


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
    annotated = getattr(log, "disposition", None)
    if annotated:
        return annotated
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
        "recipient_email": log.recipient_email or (
            log.user.email if log.user_id else ""
        ),
        "user_id": log.user_id,
        "user_email": log.user.email if log.user_id else None,
        "email_type": log.email_type,
        "subject": log.subject or "",
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
        "campaign_subject": (
            log.campaign.subject if log.campaign_id else None
        ),
        "disposition": _email_log_disposition(log),
    }
