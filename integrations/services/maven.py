"""Consent-aware, occurrence-based Maven enrollment processing (issue #960)."""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from accounts.models import TierOverride
from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from accounts.utils.display import display_name
from accounts.utils.tokens import generate_user_action_token
from community.models import CommunityAuditLog
from content.access import LEVEL_MAIN, get_user_level
from email_app.services import EmailService
from integrations.config import site_base_url
from integrations.maven_config import maven_override_duration_days, maven_override_tier_slug
from integrations.models import MavenEnrollmentEvent
from payments.models import Tier

logger = logging.getLogger(__name__)
User = get_user_model()

EVENT_ENROLLED = "user_cohort.enrolled"
EVENT_REMOVED = "user_cohort.removed"
MAX_STEP_ATTEMPTS = 3
MAX_DATABASE_CONTENTION_RETRIES = 10
RUNNING_STEP_LEASE = timedelta(minutes=15)
STEP_NAMES = ("override", "slack", "welcome", "removal")


class MavenTransientError(Exception):
    """The durable core entitlement step failed and the sender should retry."""


@dataclass
class MavenResult:
    status: str
    outcome: str = ""
    actions: list = field(default_factory=list)
    user_id: int | None = None
    created_user: bool = False


def _normalize_event_type(payload):
    for key in ("event", "type", "event_type"):
        if payload.get(key):
            return str(payload[key]).strip()
    return ""


def _extract_email(payload):
    value = payload.get("email")
    if not value and isinstance(payload.get("user"), dict):
        value = payload["user"].get("email")
    return str(value or "").strip()


def _entity(payload, name):
    raw = payload.get(name)
    explicit = payload.get(f"{name}_id") or payload.get(f"{name}Id")
    if isinstance(raw, dict):
        label = raw.get("name") or raw.get("title") or raw.get("slug") or ""
        stable = explicit or raw.get("id") or raw.get("provider_id") or raw.get("slug") or label
        return str(label).strip(), str(stable).strip().lower()
    label = str(raw or "").strip()
    return label, str(explicit or label).strip().lower()


def _extract_cohort(payload):
    return _entity(payload, "cohort")[0]


def _extract_course(payload):
    return _entity(payload, "course")[0]


def _identity(email, course_key, cohort_key):
    material = "|".join((normalize_email(email), course_key, cohort_key))
    return hashlib.sha256(material.encode()).hexdigest()


def build_dedupe_key(email, cohort, event_type, course=""):
    """Return a non-PII compatibility key; new processing is occurrence-based."""
    material = "|".join((normalize_email(email), course.strip().lower(), cohort.strip().lower(), event_type))
    return hashlib.sha256(material.encode()).hexdigest()


def _new_delivery_key(identity_hash):
    return hashlib.sha256(f"{identity_hash}|{uuid.uuid4().hex}".encode()).hexdigest()


def handle_maven_event(payload, *, dry_run=False):
    event_type = _normalize_event_type(payload)
    email = _extract_email(payload)
    course, course_key = _entity(payload, "course")
    cohort, cohort_key = _entity(payload, "cohort")
    if not email:
        raise ValueError("missing_email")
    if event_type not in {EVENT_ENROLLED, EVENT_REMOVED}:
        return MavenResult("ignored", MavenEnrollmentEvent.OUTCOME_IGNORED, [f"Event type {event_type!r} ignored."])
    if dry_run:
        return _dry_run(event_type, email, course, cohort)
    identity_hash = _identity(email, course_key, cohort_key)
    handler = _handle_enrolled if event_type == EVENT_ENROLLED else _handle_removed
    args = (payload, email, course, cohort, course_key, cohort_key, identity_hash)
    for attempt in range(MAX_DATABASE_CONTENTION_RETRIES):
        try:
            return handler(*args)
        except OperationalError as exc:
            if not _is_database_contention(exc) or attempt == MAX_DATABASE_CONTENTION_RETRIES - 1:
                raise
            logger.warning(
                "Retrying Maven occurrence after database contention (attempt %d)",
                attempt + 1,
            )
            time.sleep(0.01 * (attempt + 1))
    raise AssertionError("unreachable")


def _is_database_contention(exc):
    message = str(exc).lower()
    return any(marker in message for marker in ("locked", "deadlock", "serialization"))


def _dry_run(event_type, email, course, cohort):
    user = resolve_user_by_email(email)
    if event_type == EVENT_REMOVED:
        return MavenResult("removal_notified", actions=["Would close the active occurrence without revoking access.", "Would persist and attempt the independent removal notification step."], user_id=user.pk if user else None)
    return MavenResult(
        "already_member" if user and _is_active_community_member(user) else "onboarded",
        actions=[
            f"Would resolve {'account #' + str(user.pk) if user else 'or create a marketing-excluded account'}.",
            f"Would persist an active occurrence for {course or 'course'} / {cohort or 'cohort'}.",
            "Would grant a source-specific entitlement and run eligible delivery steps independently.",
        ],
        user_id=user.pk if user else None,
        created_user=user is None,
    )


def _handle_enrolled(payload, email, course, cohort, course_key, cohort_key, identity_hash):
    existing = resolve_user_by_email(email)
    created_user = existing is None
    already_member = bool(existing and _is_active_community_member(existing))
    occurrence = MavenEnrollmentEvent.objects.filter(
        identity_hash=identity_hash, lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
    ).first()
    created_occurrence = occurrence is None
    if occurrence is None:
        try:
            with transaction.atomic():
                user = _resolve_or_create(email)
                occurrence = MavenEnrollmentEvent.objects.create(
                    dedupe_key=_new_delivery_key(identity_hash),
                    identity_hash=identity_hash,
                    user=user,
                    email=normalize_email(email),
                    course=course,
                    cohort=cohort,
                    course_key=course_key,
                    cohort_key=cohort_key,
                    event_type=EVENT_ENROLLED,
                    outcome=(MavenEnrollmentEvent.OUTCOME_ALREADY_MEMBER if already_member else MavenEnrollmentEvent.OUTCOME_ONBOARDED),
                    payload=_safe_payload(payload),
                    welcome_eligible=not already_member,
                    slack_status=(MavenEnrollmentEvent.STEP_SKIPPED if already_member else MavenEnrollmentEvent.STEP_PENDING),
                    welcome_status=(MavenEnrollmentEvent.STEP_SKIPPED if already_member else MavenEnrollmentEvent.STEP_PENDING),
                )
        except IntegrityError:
            occurrence = MavenEnrollmentEvent.objects.get(
                identity_hash=identity_hash, lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            )
            created_occurrence = False
            created_user = False

    actions = run_occurrence_steps(occurrence)
    occurrence.refresh_from_db()
    if occurrence.override_status == MavenEnrollmentEvent.STEP_FAILED:
        raise MavenTransientError("maven entitlement step failed")
    status = "already_member" if not occurrence.welcome_eligible else "onboarded"
    if not created_occurrence and all(
        getattr(occurrence, f"{name}_status") in {MavenEnrollmentEvent.STEP_SUCCEEDED, MavenEnrollmentEvent.STEP_SKIPPED}
        for name in ("override", "slack", "welcome")
    ):
        status = "already_processed"
    return MavenResult(status, occurrence.outcome, actions, occurrence.user_id, created_user)


def _handle_removed(payload, email, course, cohort, course_key, cohort_key, identity_hash):
    now = timezone.now()
    was_already_removed = False
    with transaction.atomic():
        occurrence = (
            MavenEnrollmentEvent.objects.select_for_update()
            .filter(identity_hash=identity_hash, lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE)
            .first()
        )
        if occurrence:
            occurrence.lifecycle = MavenEnrollmentEvent.LIFECYCLE_REMOVED
            occurrence.removed_at = now
            occurrence.event_type = EVENT_REMOVED
            occurrence.removal_status = MavenEnrollmentEvent.STEP_PENDING
            occurrence.outcome = MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED
            occurrence.payload = _safe_payload(payload)
            occurrence.save(update_fields=["lifecycle", "removed_at", "event_type", "removal_status", "outcome", "payload", "updated_at"])
        else:
            occurrence = (
                MavenEnrollmentEvent.objects.filter(identity_hash=identity_hash, lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED)
                .order_by("-removed_at").first()
            )
            was_already_removed = occurrence is not None
            if occurrence is None:
                user = resolve_user_by_email(email)
                occurrence = MavenEnrollmentEvent.objects.create(
                    dedupe_key=_new_delivery_key(identity_hash), identity_hash=identity_hash,
                    user=user, email=normalize_email(email), course=course, cohort=cohort,
                    course_key=course_key, cohort_key=cohort_key, event_type=EVENT_REMOVED,
                    lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED, removed_at=now,
                    outcome=MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED,
                    override_status=MavenEnrollmentEvent.STEP_SKIPPED,
                    slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
                    welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
                    removal_status=MavenEnrollmentEvent.STEP_PENDING,
                    payload=_safe_payload(payload),
                )
    actions = run_occurrence_steps(occurrence, step="removal")
    return MavenResult(
        "already_processed" if was_already_removed else "removal_notified",
        occurrence.outcome, actions, occurrence.user_id,
    )


def _resolve_or_create(email):
    user = resolve_user_by_email(email)
    if user is not None:
        return user
    return User.objects.create_user(
        email=normalize_email(email), password=None, email_verified=False,
        signup_source="imported", unsubscribed=True,
        email_preferences={"newsletter": False, "maven_emails": True},
    )


def _grant_or_refresh_override(user, tier, target_expiry, cohort, course, *, source=""):
    """Grant Maven access without replacing, lowering, or shortening any grant."""
    source = source or f"maven:{build_dedupe_key(user.email, cohort, EVENT_ENROLLED, course)}"
    grant = TierOverride.objects.filter(user=user, source=source).first()
    if grant is None:
        # Source tracking was added after the first Maven release. Adopt a
        # same-tier legacy grant to avoid stacking it; different/stronger
        # grants remain independent and are never deactivated.
        grant = (
            TierOverride.objects.filter(user=user, source="", override_tier=tier)
            .order_by("-expires_at").first()
        )
        if grant is not None:
            grant.source = source
            grant.is_active = True
            grant.save(update_fields=["source", "is_active"])
    if grant:
        changed = []
        if grant.override_tier.level < tier.level:
            grant.override_tier = tier
            changed.append("override_tier")
        if grant.expires_at < target_expiry:
            grant.expires_at = target_expiry
            changed.append("expires_at")
        if changed:
            grant.save(update_fields=changed)
            _audit_override(user, tier, grant.expires_at, cohort, course, refreshed=True)
            return f"Extended Maven entitlement to {grant.expires_at.isoformat()}."
        return "Maven entitlement already satisfies the requested tier and duration."
    TierOverride.objects.create(
        user=user, original_tier=user.tier, override_tier=tier, expires_at=target_expiry,
        granted_by=None, is_active=True, source=source,
    )
    _audit_override(user, tier, target_expiry, cohort, course, refreshed=False)
    return f"Granted Maven entitlement through {target_expiry.isoformat()}."


def _audit_override(user, tier, expiry, cohort, course, *, refreshed):
    CommunityAuditLog.objects.create(
        user=user, action="maven_enrollment_override",
        details=f"tier={tier.slug} expires_at={expiry.isoformat()} cohort={cohort or '—'} course={course or '—'} refreshed={'yes' if refreshed else 'no'}",
    )


def _is_active_community_member(user):
    return bool(getattr(user, "slack_member", False) and get_user_level(user) >= LEVEL_MAIN)


def _invite_to_slack(user, actions):
    from community.services.slack import get_community_service
    get_community_service().invite(user)
    actions.append("Invited to Slack community.")


def _send_welcome(user, course, actions):
    EmailService().send(user, "maven_welcome", _welcome_context(user, course))
    actions.append("Sent maven_welcome email.")


def _welcome_context(user, course):
    site_url = site_base_url().rstrip("/")
    reset_token = generate_user_action_token(user.pk, "password_reset", expiry_hours=24)
    opt_out_token = generate_user_action_token(user.pk, "maven_email_opt_out")
    return {
        "user_name": display_name(user), "course_name": course or "your course",
        "password_reset_url": f"{site_url}/api/password-reset?token={reset_token}",
        "sign_in_url": f"{site_url}/accounts/login/",
        "opt_out_url": f"{site_url}/api/maven-email-opt-out?token={opt_out_token}",
    }


def run_occurrence_steps(occurrence, *, step=None, force=False):
    """Run retryable steps. Successful/skipped steps are never repeated."""
    actions = []
    if step:
        _run_step(occurrence.pk, step, actions, force=force)
        return actions
    if occurrence.lifecycle == MavenEnrollmentEvent.LIFECYCLE_REMOVED:
        _run_step(occurrence.pk, "removal", actions, force=force)
        return actions
    # Access is the durable core. Do not send visible onboarding actions until
    # it has succeeded; concurrent duplicate deliveries will observe RUNNING
    # and leave those later steps for the winning worker.
    _run_step(occurrence.pk, "override", actions, force=force)
    occurrence.refresh_from_db(fields=["override_status"])
    if occurrence.override_status != MavenEnrollmentEvent.STEP_SUCCEEDED:
        return actions
    for name in ("slack", "welcome"):
        _run_step(occurrence.pk, name, actions, force=force)
    return actions


def _run_step(pk, name, actions, *, force=False):
    if name not in STEP_NAMES:
        raise ValueError("unknown Maven step")
    status_field = f"{name}_status"
    attempts_field = f"{name}_attempts"
    attempted_field = f"{name}_attempted_at"
    completed_field = f"{name}_completed_at"
    error_field = f"{name}_error"
    with transaction.atomic():
        row = MavenEnrollmentEvent.objects.select_for_update().select_related("user").get(pk=pk)
        status = getattr(row, status_field)
        attempts = getattr(row, attempts_field)
        if status in {row.STEP_SUCCEEDED, row.STEP_SKIPPED}:
            return
        if status == row.STEP_RUNNING:
            attempted_at = getattr(row, attempted_field)
            if attempted_at and attempted_at > timezone.now() - RUNNING_STEP_LEASE:
                actions.append(f"{name.title()} is already running; not repeated.")
                return
        if attempts >= MAX_STEP_ATTEMPTS and not force:
            actions.append(f"{name.title()} retry limit reached.")
            return
        setattr(row, status_field, row.STEP_RUNNING)
        setattr(row, attempts_field, attempts + 1)
        setattr(row, attempted_field, timezone.now())
        setattr(row, completed_field, None)
        setattr(row, error_field, "")
        row.save(
            update_fields=[
                status_field,
                attempts_field,
                attempted_field,
                completed_field,
                error_field,
                "updated_at",
            ]
        )
    try:
        if name == "override":
            tier = Tier.objects.get(slug=maven_override_tier_slug())
            expiry = row.created_at + timedelta(days=maven_override_duration_days())
            actions.append(_grant_or_refresh_override(row.user, tier, expiry, row.cohort, row.course, source=f"maven:{row.identity_hash}"))
        elif name == "slack":
            _invite_to_slack(row.user, actions)
        elif name == "welcome":
            if not row.user.email_preferences.get("maven_emails", True):
                _finish_step(pk, name, MavenEnrollmentEvent.STEP_SKIPPED, "")
                actions.append("Maven welcome suppressed by scoped preference.")
                return
            _send_welcome(row.user, row.course, actions)
        else:
            from community.services.staff_notifications import notify_maven_cohort_removal
            notify_maven_cohort_removal(row.user, row.cohort, row.course, email=row.email)
            actions.append("Sent staff removal heads-up.")
    except Exception as exc:
        logger.exception("Maven %s step failed for occurrence %s", name, pk)
        _finish_step(pk, name, MavenEnrollmentEvent.STEP_FAILED, _safe_error(exc))
        actions.append(f"{name.title()} failed; persisted for retry.")
    else:
        _finish_step(pk, name, MavenEnrollmentEvent.STEP_SUCCEEDED, "")


def _finish_step(pk, name, status, error):
    now = timezone.now()
    MavenEnrollmentEvent.objects.filter(pk=pk).update(
        **{
            f"{name}_status": status,
            f"{name}_error": error,
            f"{name}_completed_at": now,
            "updated_at": now,
        }
    )


def _safe_error(exc):
    return exc.__class__.__name__[:255]


def _safe_payload(payload):
    """Persist only operational metadata; never retain email, user data, or secrets."""
    safe = {"event": _normalize_event_type(payload)}
    for name in ("course", "cohort"):
        label, key = _entity(payload, name)
        safe[name] = {"key": key, "label": label}
    for key in ("event_id", "id", "created_at"):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
    return safe
