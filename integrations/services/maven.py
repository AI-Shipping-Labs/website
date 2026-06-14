"""Maven cohort webhook handling (issue #960).

Single source of truth for the onboarding / removal logic. The webhook view
(``integrations/views/maven_webhook.py``) and the ``replay_maven_event``
management command both call into here, so a dry-run and a real delivery
exercise exactly the same code path.

Flow summary
============

``user_cohort.enrolled``
    Resolve or create the account, grant/refresh a long-lived ``main`` tier
    override through the shipped grant pipeline, invite to Slack, and send the
    course-framed ``maven_welcome`` email. For an enrollee who is ALREADY a
    community member (active access + in Slack) we do nothing visible — no
    email, no staff note, no re-invite — but we still silently refresh/extend
    the override if it lapsed or would expire before the cohort.

``user_cohort.removed``
    Make NO change to access. Send a staff heads-up so a human decides.

Any other event type (``payment.success``, ``user_cohort.unenrolled``, ...)
is ignored.

Idempotency
===========

Every terminal success writes one ``MavenEnrollmentEvent`` row keyed on the
normalized email + cohort + event type. A repeat delivery hits the row and
short-circuits with ``already_processed`` before any side effect runs. The row
is written ONLY after the work succeeds (Stripe-webhook ordering), so a
transient failure (signalled by raising) leaves no row and the sender retries.
"""

import datetime
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import TierOverride
from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from community.models import CommunityAuditLog
from content.access import LEVEL_MAIN
from email_app.services import EmailService
from integrations.config import site_base_url
from integrations.maven_config import (
    maven_override_duration_days,
    maven_override_tier_slug,
)
from integrations.models import MavenEnrollmentEvent
from payments.models import Tier

logger = logging.getLogger(__name__)

User = get_user_model()

EVENT_ENROLLED = "user_cohort.enrolled"
EVENT_REMOVED = "user_cohort.removed"


class MavenTransientError(Exception):
    """Raised when processing failed in a way the sender should retry.

    The webhook view maps this to a ``500`` so Maven/Zapier re-deliver. No
    ``MavenEnrollmentEvent`` row is written, so the retry runs the side
    effects again from a clean state.
    """


@dataclass
class MavenResult:
    """Outcome of handling one Maven event."""

    status: str  # already_processed / onboarded / refreshed / already_member / removal_notified / ignored
    outcome: str = ""
    actions: list = field(default_factory=list)  # human-readable lines for dry-run / logging
    user_id: int | None = None
    created_user: bool = False


def _normalize_event_type(payload):
    """Pull the event type from the documented / Zapier-flattened shapes."""
    for key in ("event", "type", "event_type"):
        value = payload.get(key)
        if value:
            return str(value).strip()
    return ""


def _extract_email(payload):
    """Pull the enrollee email from the documented / nested shapes."""
    email = payload.get("email")
    if not email:
        user = payload.get("user")
        if isinstance(user, dict):
            email = user.get("email")
    return (email or "").strip()


def _extract_cohort(payload):
    cohort = payload.get("cohort")
    if isinstance(cohort, dict):
        cohort = cohort.get("name") or cohort.get("id") or cohort.get("slug")
    return (str(cohort).strip() if cohort else "")


def _extract_course(payload):
    course = payload.get("course")
    if isinstance(course, dict):
        course = course.get("name") or course.get("title") or course.get("slug")
    return (str(course).strip() if course else "")


def build_dedupe_key(email, cohort, event_type):
    """Stable dedupe key: normalized email + cohort + event type."""
    return f"{normalize_email(email)}|{cohort.strip().lower()}|{event_type}"


def handle_maven_event(payload, *, dry_run=False):
    """Process one Maven webhook payload. Returns a ``MavenResult``.

    Raises ``MavenTransientError`` only for retryable failures; the caller
    maps that to a ``500``. A missing email raises ``ValueError`` (the caller
    maps it to ``400``).
    """
    event_type = _normalize_event_type(payload)
    email = _extract_email(payload)
    cohort = _extract_cohort(payload)
    course = _extract_course(payload)

    if not email:
        raise ValueError("missing_email")

    if event_type == EVENT_ENROLLED:
        return _handle_enrolled(payload, email, cohort, course, dry_run=dry_run)
    if event_type == EVENT_REMOVED:
        return _handle_removed(payload, email, cohort, course, dry_run=dry_run)

    # Any other recognizable type acknowledges and does nothing.
    return MavenResult(
        status="ignored",
        outcome=MavenEnrollmentEvent.OUTCOME_IGNORED,
        actions=[f"Event type {event_type!r} is not actionable — ignored."],
    )


# ---------------------------------------------------------------------
# user_cohort.enrolled
# ---------------------------------------------------------------------

def _handle_enrolled(payload, email, cohort, course, *, dry_run):
    dedupe_key = build_dedupe_key(email, cohort, EVENT_ENROLLED)

    if not dry_run and MavenEnrollmentEvent.objects.filter(dedupe_key=dedupe_key).exists():
        return MavenResult(
            status="already_processed",
            actions=[f"Dedupe key {dedupe_key!r} already processed."],
        )

    actions = []
    existing = resolve_user_by_email(email)
    tier_slug = maven_override_tier_slug()
    tier = Tier.objects.filter(slug=tier_slug).first()
    if tier is None:  # pragma: no cover - maven_override_tier_slug validates this
        raise MavenTransientError(f"override tier {tier_slug!r} not found")

    duration_days = maven_override_duration_days()
    target_expiry = timezone.now() + timedelta(days=duration_days)

    # Is the enrollee already a fully-onboarded community member? They must
    # already have active access (paid base tier or active override) AND be in
    # the Slack community. For those we suppress ALL comms but still guarantee
    # access by refreshing/extending a lapsed/expiring override.
    already_member = existing is not None and _is_active_community_member(existing)

    if dry_run:
        return _dry_run_enrolled(
            existing, email, tier, target_expiry, cohort, course, already_member,
        )

    created_user = existing is None
    try:
        with transaction.atomic():
            user = _resolve_or_create(email)
            override_outcome = _grant_or_refresh_override(
                user, tier, target_expiry, cohort, course,
            )
            actions.append(override_outcome)

            if not already_member:
                _invite_to_slack(user, actions)
                _send_welcome(user, course, actions)
                outcome = MavenEnrollmentEvent.OUTCOME_ONBOARDED
                status = "onboarded"
            else:
                actions.append(
                    "Already an active community member — no welcome email, "
                    "no staff note, no re-invite."
                )
                outcome = MavenEnrollmentEvent.OUTCOME_ALREADY_MEMBER
                status = "already_member"

            MavenEnrollmentEvent.objects.create(
                dedupe_key=dedupe_key,
                email=normalize_email(email),
                course=course,
                cohort=cohort,
                event_type=EVENT_ENROLLED,
                outcome=outcome,
                payload=_safe_payload(payload),
            )
    except MavenTransientError:
        raise
    except Exception as exc:  # noqa: BLE001 - any unexpected failure is retryable
        logger.exception("Maven enrollment processing failed for %s", email)
        raise MavenTransientError(str(exc)) from exc

    return MavenResult(
        status=status,
        outcome=outcome,
        actions=actions,
        user_id=user.pk,
        created_user=created_user,
    )


def _dry_run_enrolled(existing, email, tier, target_expiry, cohort, course, already_member):
    actions = []
    if existing is None:
        actions.append(f"Would CREATE account for {email} (signup_source=imported).")
    else:
        actions.append(
            f"Would RESOLVE to existing account #{existing.pk} ({existing.email})."
        )

    active = (
        TierOverride.objects.filter(
            user=existing, is_active=True, expires_at__gt=timezone.now(),
        ).first()
        if existing is not None
        else None
    )
    if active and active.override_tier_id == tier.id and active.expires_at >= target_expiry:
        actions.append(
            f"Override on {tier.slug} already active until "
            f"{active.expires_at.isoformat()} (>= target) — would leave unchanged."
        )
    elif active and active.override_tier_id == tier.id:
        actions.append(
            f"Would EXTEND existing {tier.slug} override to "
            f"~{target_expiry.isoformat()}."
        )
    else:
        actions.append(
            f"Would GRANT {tier.slug} override expiring ~{target_expiry.isoformat()}."
        )

    if already_member:
        actions.append(
            "Already an active community member — would SKIP welcome email, "
            "staff note, and re-invite (override still refreshed)."
        )
        status = "already_member"
    else:
        actions.append("Would INVITE to Slack community channels.")
        actions.append(f"Would SEND maven_welcome email (course={course or '—'}).")
        status = "onboarded"
    actions.append("Would WRITE MavenEnrollmentEvent dedupe row.")
    return MavenResult(
        status=status,
        actions=actions,
        user_id=existing.pk if existing else None,
        created_user=existing is None,
    )


def _resolve_or_create(email):
    """Resolve the canonical account or create a Free imported one."""
    user = resolve_user_by_email(email)
    if user is not None:
        return user
    normalized = normalize_email(email)
    # ``unsubscribed`` left at its default (False) — but the account is
    # email_verified=False, so the campaign audience query (which requires
    # email_verified=True) never counts it as a marketing subscriber.
    return User.objects.create_user(
        email=normalized,
        password=None,
        email_verified=False,
        signup_source="imported",
    )


def _grant_or_refresh_override(user, tier, target_expiry, cohort, course):
    """Grant or extend the override; enforce one-active-override; audit it.

    Returns a human-readable action line. Never stacks: an existing active
    override on the same tier is extended in place when the new window is
    longer, and never shortened.
    """
    now = timezone.now()
    active = (
        TierOverride.objects.filter(user=user, is_active=True, expires_at__gt=now)
        .select_related("override_tier")
        .first()
    )

    if active and active.override_tier_id == tier.id:
        if active.expires_at >= target_expiry:
            return (
                f"Override on {tier.slug} already active until "
                f"{active.expires_at.isoformat()} — left unchanged."
            )
        active.expires_at = target_expiry
        active.save(update_fields=["expires_at"])
        _audit_override(user, tier, target_expiry, cohort, course, refreshed=True)
        return f"Extended {tier.slug} override to {target_expiry.isoformat()}."

    # Different active override (or none): deactivate any active one and grant
    # a fresh row — preserves the one-active-override invariant.
    TierOverride.objects.filter(user=user, is_active=True).update(is_active=False)
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=tier,
        expires_at=target_expiry,
        granted_by=None,
        is_active=True,
    )
    _audit_override(user, tier, target_expiry, cohort, course, refreshed=False)
    return f"Granted {tier.slug} override expiring {target_expiry.isoformat()}."


def _audit_override(user, tier, expiry, cohort, course, *, refreshed):
    CommunityAuditLog.objects.create(
        user=user,
        action="maven_enrollment_override",
        details=(
            f"tier={tier.slug} expires_at={expiry.isoformat()} "
            f"cohort={cohort or '—'} course={course or '—'} "
            f"refreshed={'yes' if refreshed else 'no'}"
        ),
    )


def _is_active_community_member(user):
    """True iff the user has active access AND is in the Slack community.

    Active access = a paid base tier (level >= Main) or an active, unexpired
    tier override at Main or above. Community = ``slack_member`` is True.
    """
    if not getattr(user, "slack_member", False):
        return False

    base_level = user.tier.level if user.tier_id else 0
    if base_level >= LEVEL_MAIN:
        return True

    return TierOverride.objects.filter(
        user=user,
        is_active=True,
        expires_at__gt=timezone.now(),
        override_tier__level__gte=LEVEL_MAIN,
    ).exists()


def _invite_to_slack(user, actions):
    """Best-effort idempotent Slack invite. Never blocks the rest of the flow."""
    try:
        # Inline import: ``community.services.slack`` pulls in a wide
        # dependency graph; keeping it lazy avoids an import cycle at module
        # load and keeps the handler importable from the settings registry path.
        from community.services.slack import get_community_service  # noqa: PLC0415

        get_community_service().invite(user)
        actions.append("Invited to Slack community.")
    except Exception:  # noqa: BLE001 - best-effort, mirrors notify_paid_signup
        logger.exception("Maven onboarding: Slack invite failed for %s", user.email)
        actions.append("Slack invite failed (logged) — continuing.")


def _send_welcome(user, course, actions):
    """Best-effort course-framed welcome email. Never blocks the flow."""
    try:
        EmailService().send(user, "maven_welcome", _welcome_context(user, course))
        actions.append("Sent maven_welcome email.")
    except Exception:  # noqa: BLE001 - best-effort, mirrors notify_paid_signup
        logger.exception("Maven onboarding: welcome email failed for %s", user.email)
        actions.append("Welcome email failed (logged) — continuing.")


def _welcome_context(user, course):
    site_url = site_base_url().rstrip("/")
    display_name = user.first_name or (user.email.split("@", 1)[0] if user.email else "")

    reset_payload = {
        "user_id": user.pk,
        "action": "password_reset",
        "exp": (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
        ),
    }
    reset_token = jwt.encode(reset_payload, settings.SECRET_KEY, algorithm="HS256")

    # Welcome emails get no auto unsubscribe footer (transactional/welcome),
    # so we mint the opt-out link in-template using the same JWT mechanism the
    # promotional unsubscribe path uses.
    opt_out_token = jwt.encode(
        {"user_id": user.pk, "action": "unsubscribe"},
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    return {
        "user_name": display_name,
        "course_name": course or "your course",
        "password_reset_url": f"{site_url}/api/password-reset?token={reset_token}",
        "sign_in_url": f"{site_url}/login/",
        "opt_out_url": f"{site_url}/api/unsubscribe?token={opt_out_token}",
    }


# ---------------------------------------------------------------------
# user_cohort.removed
# ---------------------------------------------------------------------

def _handle_removed(payload, email, cohort, course, *, dry_run):
    dedupe_key = build_dedupe_key(email, cohort, EVENT_REMOVED)
    user = resolve_user_by_email(email)

    if dry_run:
        actions = []
        if user is None:
            actions.append(
                f"No account matches {email} — would send a lighter "
                "'unknown user removed' staff note (NO access change)."
            )
        else:
            actions.append(
                f"Would send staff heads-up about removal of account "
                f"#{user.pk} ({user.email}) from cohort {cohort or '—'}."
            )
        actions.append("Would make NO change to override / access / Slack.")
        actions.append("Would WRITE MavenEnrollmentEvent dedupe row.")
        return MavenResult(
            status="removal_notified",
            actions=actions,
            user_id=user.pk if user else None,
        )

    if MavenEnrollmentEvent.objects.filter(dedupe_key=dedupe_key).exists():
        return MavenResult(
            status="already_processed",
            actions=[f"Dedupe key {dedupe_key!r} already processed."],
        )

    actions = ["No change made to override / access / Slack membership."]
    try:
        # Inline import: ``community.services.staff_notifications`` reaches into
        # email/Slack/analytics; lazy import avoids a load-time cycle.
        from community.services.staff_notifications import (  # noqa: PLC0415
            notify_maven_cohort_removal,
        )

        notify_maven_cohort_removal(user, cohort, course, email=email)
        actions.append("Sent staff removal heads-up (best-effort).")
    except Exception:  # noqa: BLE001 - best-effort; must never 500 the webhook
        logger.exception("Maven removal: staff notification failed for %s", email)
        actions.append("Staff notification failed (logged).")

    MavenEnrollmentEvent.objects.create(
        dedupe_key=dedupe_key,
        email=normalize_email(email),
        course=course,
        cohort=cohort,
        event_type=EVENT_REMOVED,
        outcome=MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED,
        payload=_safe_payload(payload),
    )
    return MavenResult(
        status="removal_notified",
        outcome=MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED,
        actions=actions,
        user_id=user.pk if user else None,
    )


def _safe_payload(payload):
    """Return a JSON-serialisable copy of the payload for storage."""
    try:
        json.dumps(payload)
        return payload
    except (TypeError, ValueError):
        return {"_unserializable": str(payload)[:2000]}
