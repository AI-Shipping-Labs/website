"""Consent-aware, occurrence-based Maven enrollment processing (issue #960)."""

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone

from accounts.models.user import SIGNUP_SOURCE_MAVEN_WEBHOOK
from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from accounts.utils.tags import add_tag, normalize_tag, remove_tag
from community.models import CommunityAuditLog
from content.access import LEVEL_MAIN, get_user_level
from content.models import CourseAccess
from content.models.cohort import CohortEnrollment
from content.services.course_cohorts import apply_cohort_enrollment_tags
from content.services.enrollment import UNENROLL_CAUSE_ACCESS_LOST, record_unenrollment
from email_app.package_mail import send_package_mail
from events.services.event_audience import email_skip_status
from integrations.config import get_config, validate_email_config_value
from integrations.maven_config import (
    maven_course_tag_prefix,
    maven_override_duration_days,
    maven_override_tier_slug,
    maven_removal_revokes_override,
    maven_removal_student_email_enabled,
)
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven_matching import (
    MavenUnknownCohortError,
    MavenUnknownCourseError,
    claim_resolution_match,
    close_split_siblings,
    occurrence_signature,
    resolve_maven_cohort,
    resolve_maven_course,
    same_enrollment,
)
from payments.models import Tier, TierOverride
from payments.services.tier_override_revoke import (
    active_overrides_for,
    revoke_tier_override,
)

logger = logging.getLogger(__name__)
User = get_user_model()

EVENT_ENROLLED = "user_cohort.enrolled"
EVENT_REMOVED = "user_cohort.removed"
MAX_STEP_ATTEMPTS = 3
MAX_DATABASE_CONTENTION_RETRIES = 10
RUNNING_STEP_LEASE = timedelta(minutes=15)
# ``tagging`` runs first and gates nothing: "this person arrived via Maven"
# is a member-invisible CRM fact that must survive a failed entitlement
# (issue #1732).
STEP_NAMES = (
    "tagging", "override", "enrollment", "notification", "welcome", "slack", "removal",
)
_SQLITE_DELIVERY_LOCK = threading.Lock()


class MavenTransientError(Exception):
    """The durable core entitlement step failed and the sender should retry."""


@dataclass
class MavenResult:
    status: str
    outcome: str = ""
    actions: list = field(default_factory=list)
    user_id: int | None = None
    created_user: bool = False
    occurrence_id: int | None = None
    exhausted_steps: list = field(default_factory=list)


@dataclass(frozen=True)
class MavenStepRetryResult:
    """Persisted outcome of one operator-requested step retry."""

    step: str
    outcome: str
    attempted: bool
    reason: str = ""


# Maven exposes no documented payload contract and we have never seen a real
# delivery, so intake tolerates the obvious envelope shapes rather than
# dropping an enrollee. ``student`` / ``member`` are accepted alongside
# ``user`` for the nested person object, and a single ``data`` / ``payload``
# wrapper is unwrapped (one level only) when the outer object carries no
# resolvable email.
PERSON_KEYS = ("user", "student", "member")
ENVELOPE_KEYS = ("data", "payload")

# ``User.first_name`` / ``User.last_name`` are CharField(max_length=150).
# An over-long value from an unvalidated third-party payload would raise a
# Postgres DataError, return a 500, and put Maven into a redelivery loop, so
# names are truncated at intake rather than trusted.
NAME_MAX_LENGTH = 150


def _has_direct_email(payload):
    if payload.get("email"):
        return True
    return any(
        isinstance(payload.get(key), dict) and payload[key].get("email")
        for key in PERSON_KEYS
    )


def normalize_payload(payload):
    """Return a flat view of the payload, unwrapping one envelope level.

    The inner object wins only where the outer object has no value for a
    key, so a real top-level ``event`` beside a ``data`` body still
    resolves. Only applied when the outer object carries no email at all.
    """
    if not isinstance(payload, dict) or _has_direct_email(payload):
        return payload
    for key in ENVELOPE_KEYS:
        inner = payload.get(key)
        if isinstance(inner, dict) and _has_direct_email(inner):
            merged = dict(inner)
            merged.update(
                {
                    k: v
                    for k, v in payload.items()
                    if k not in ENVELOPE_KEYS and v not in (None, "")
                }
            )
            return merged
    return payload


def _normalize_event_type(payload):
    for key in ("event", "type", "event_type"):
        if payload.get(key):
            return str(payload[key]).strip()
    return ""


def _extract_email(payload):
    value = payload.get("email")
    if not value:
        for key in PERSON_KEYS:
            nested = payload.get(key)
            if isinstance(nested, dict) and nested.get("email"):
                value = nested["email"]
                break
    return str(value or "").strip()


def _extract_name(payload):
    """Return ``(first_name, last_name)`` from the payload.

    Accepts ``first_name`` / ``last_name`` on a nested person object or at
    the top level, or a single ``name`` / ``full_name`` split on the first
    space. Both values are truncated to ``NAME_MAX_LENGTH``. Names are PII:
    the caller must never persist them into ``MavenEnrollmentEvent.payload``
    or write them to a log line.
    """
    sources = [payload]
    sources.extend(
        payload[key] for key in PERSON_KEYS if isinstance(payload.get(key), dict)
    )
    for source in sources:
        first = str(source.get("first_name") or "").strip()
        last = str(source.get("last_name") or "").strip()
        if first or last:
            return _clip_name(first), _clip_name(last)
    for source in sources:
        whole = str(source.get("name") or source.get("full_name") or "").strip()
        if whole:
            first, _, last = whole.partition(" ")
            return _clip_name(first.strip()), _clip_name(last.strip())
    return "", ""


def _clip_name(value):
    return value[:NAME_MAX_LENGTH]


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


def normalized_event_type(payload):
    """The event-type string as intake resolves it. Never PII."""
    return _normalize_event_type(normalize_payload(payload))


def payload_key_names(payload):
    """Sorted top-level key NAMES of a payload — never any value.

    Used by the webhook and the missing-course warning so an operator can
    debug an unexpected Maven envelope without any PII reaching the logs.
    """
    if not isinstance(payload, dict):
        return []
    return sorted(str(key) for key in payload)


def handle_maven_event(payload, *, dry_run=False):
    payload = normalize_payload(payload)
    event_type = _normalize_event_type(payload)
    email = _extract_email(payload)
    course, course_key = _entity(payload, "course")
    cohort, cohort_key = _entity(payload, "cohort")
    if not email:
        raise ValueError("missing_email")
    if event_type not in {EVENT_ENROLLED, EVENT_REMOVED}:
        return MavenResult("ignored", MavenEnrollmentEvent.OUTCOME_IGNORED, [f"Event type {event_type!r} ignored."])
    if event_type == EVENT_ENROLLED and not (course or cohort):
        # The enrollee-facing subject falls back to the generic welcome.
        # Key NAMES only so the extractor can be corrected without any
        # payload value (email, name, cohort label) reaching the logs.
        logger.warning(
            "Maven enrolled payload carried no course or cohort label; "
            "top-level keys: %s",
            ", ".join(payload_key_names(payload)) or "(none)",
        )
    if dry_run:
        return _dry_run(event_type, email, course, cohort)
    identity_hash = _identity(email, course_key, cohort_key)
    handler = _handle_enrolled if event_type == EVENT_ENROLLED else _handle_removed
    args = (payload, email, course, cohort, course_key, cohort_key, identity_hash)

    # SQLite has a single writer. Serializing deliveries within one process
    # prevents two valid webhook requests from repeatedly colliding while
    # they create the occurrence and claim its durable steps. Other database
    # engines retain normal concurrency, and the retry loop below still
    # protects SQLite when contention comes from another process.
    if connection.vendor == "sqlite":
        with _SQLITE_DELIVERY_LOCK:
            return _handle_with_contention_retries(handler, args)
    return _handle_with_contention_retries(handler, args)


def _handle_with_contention_retries(handler, args):
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
        return MavenResult("removal_notified", actions=["Would close the active occurrence and revoke its course access, cohort membership, and Maven-granted tier override (manual overrides, paid tiers, and Slack membership are never touched).", "Would email the removed student and send staff a summary of what was changed."], user_id=user.pk if user else None)
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
        first_name, last_name = _extract_name(payload)
        try:
            with transaction.atomic():
                user = _resolve_or_create(email, first_name, last_name)
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
                    account_created=created_user,
                    tagging_status=MavenEnrollmentEvent.STEP_PENDING,
                    enrollment_status=MavenEnrollmentEvent.STEP_PENDING,
                    notification_status=MavenEnrollmentEvent.STEP_PENDING,
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
    exhausted_steps = incomplete_steps_at_attempt_ceiling(occurrence)
    if occurrence.override_status == MavenEnrollmentEvent.STEP_FAILED and not exhausted_steps:
        raise MavenTransientError("maven entitlement step failed")
    if exhausted_steps:
        return MavenResult(
            "manual_intervention_required",
            occurrence.outcome,
            actions,
            occurrence.user_id,
            created_user,
            occurrence.pk,
            exhausted_steps,
        )
    status = "already_member" if not occurrence.welcome_eligible else "onboarded"
    if not created_occurrence and all(
        getattr(occurrence, f"{name}_status") in {MavenEnrollmentEvent.STEP_SUCCEEDED, MavenEnrollmentEvent.STEP_SKIPPED}
        for name in ("tagging", "override", "enrollment", "notification", "slack", "welcome")
    ):
        status = "already_processed"
    return MavenResult(
        status,
        occurrence.outcome,
        actions,
        occurrence.user_id,
        created_user,
        occurrence.pk,
    )


def _handle_removed(payload, email, course, cohort, course_key, cohort_key, identity_hash):
    now = timezone.now()
    was_already_removed = False
    with transaction.atomic():
        occurrence = _active_occurrence_for_removal(email, course, cohort, course_key, cohort_key, identity_hash, now)
        if occurrence:
            _close_occurrence(occurrence, payload, now)
        else:
            occurrence = (
                MavenEnrollmentEvent.objects.filter(identity_hash=identity_hash, lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED)
                .order_by("-removed_at").first()
            )
            was_already_removed = occurrence is not None
            if occurrence is None:
                occurrence = _create_removed_occurrence(payload, email, course, cohort, course_key, cohort_key, identity_hash, now)
    actions = run_occurrence_steps(occurrence, step="removal")
    occurrence.refresh_from_db()
    exhausted_steps = incomplete_steps_at_attempt_ceiling(occurrence)
    if exhausted_steps:
        return MavenResult(
            "manual_intervention_required",
            occurrence.outcome,
            actions,
            occurrence.user_id,
            False,
            occurrence.pk,
            exhausted_steps,
        )
    return MavenResult(
        "already_processed" if was_already_removed else "removal_notified",
        occurrence.outcome, actions, occurrence.user_id, False, occurrence.pk,
    )


def _active_occurrence_for_removal(email, course, cohort, course_key, cohort_key, identity_hash, now):
    """The active enrollment this removal closes: exact hash, else resolution match."""
    occurrence = (
        MavenEnrollmentEvent.objects.select_for_update()
        .filter(identity_hash=identity_hash, lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE)
        .first()
    )
    if occurrence is not None:
        return occurrence
    user_id = None
    user = resolve_user_by_email(email)
    if user is not None:
        user_id = user.pk
    return claim_resolution_match(
        user_id, email,
        (course_key, cohort_key, course, cohort), identity_hash, now=now,
    )


def _close_occurrence(occurrence, payload, now):
    occurrence.lifecycle = MavenEnrollmentEvent.LIFECYCLE_REMOVED
    occurrence.removed_at = now
    occurrence.event_type = EVENT_REMOVED
    occurrence.removal_status = MavenEnrollmentEvent.STEP_PENDING
    occurrence.outcome = MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED
    occurrence.payload = _safe_payload(payload)
    occurrence.save(update_fields=["lifecycle", "removed_at", "event_type", "removal_status", "outcome", "payload", "updated_at"])


def _create_removed_occurrence(payload, email, course, cohort, course_key, cohort_key, identity_hash, now):
    return MavenEnrollmentEvent.objects.create(
        dedupe_key=_new_delivery_key(identity_hash), identity_hash=identity_hash,
        user=resolve_user_by_email(email), email=normalize_email(email), course=course, cohort=cohort,
        course_key=course_key, cohort_key=cohort_key, event_type=EVENT_REMOVED,
        lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED, removed_at=now,
        outcome=MavenEnrollmentEvent.OUTCOME_REMOVAL_NOTIFIED,
        override_status=MavenEnrollmentEvent.STEP_SKIPPED,
        slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
        welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        removal_status=MavenEnrollmentEvent.STEP_PENDING,
        payload=_safe_payload(payload),
    )


def incomplete_steps_at_attempt_ceiling(occurrence):
    """Return incomplete step names whose automatic-attempt budget is spent."""
    terminal = {
        MavenEnrollmentEvent.STEP_SUCCEEDED,
        MavenEnrollmentEvent.STEP_SKIPPED,
    }
    return [
        name
        for name in STEP_NAMES
        if getattr(occurrence, f"{name}_status") not in terminal
        and getattr(occurrence, f"{name}_attempts") >= MAX_STEP_ATTEMPTS
    ]


def _resolve_or_create(email, first_name="", last_name=""):
    """Resolve or create the enrollee, filling only blank name fields.

    A name the member set themselves is never overwritten. Names are PII
    and are stored only on the ``User`` row — never in the ledger payload
    and never in a log line.
    """
    user = resolve_user_by_email(email)
    if user is not None:
        _fill_blank_names(user, first_name, last_name)
        return user
    return User.objects.create_user(
        email=normalize_email(email), password=None, email_verified=False,
        first_name=first_name, last_name=last_name,
        # Enrolling in a course is not newsletter consent, so a new account
        # starts marketing-excluded and stays that way until the enrollee
        # affirmatively opts in from the welcome email (issue #1593). No
        # branch here ever writes these fields on an account we resolved
        # rather than created.
        # Issue #1732: a webhook-created account is not a bulk import, and
        # Studio must not label it "Bulk import (Stripe / CSV / course DB)".
        signup_source=SIGNUP_SOURCE_MAVEN_WEBHOOK, unsubscribed=True,
        email_preferences={"newsletter": False, "maven_emails": True},
    )


def _fill_blank_names(user, first_name, last_name):
    updates = []
    if first_name and not (user.first_name or "").strip():
        user.first_name = first_name
        updates.append("first_name")
    if last_name and not (user.last_name or "").strip():
        user.last_name = last_name
        updates.append("last_name")
    if updates:
        user.save(update_fields=updates)


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
        if not grant.is_active:
            # A removal revoked this source's grant; re-enrolling in the same
            # course/cohort restores it rather than leaving it inactive.
            grant.is_active = True
            changed.append("is_active")
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
        # Issue #1579: the base tier lives on payments.Membership.
        user=user, original_tier=user.membership.tier, override_tier=tier, expires_at=target_expiry,
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


# Issue #1665: the Maven ``slack`` step no longer calls the Slack API
# (``users.lookupByEmail`` / ``conversations.invite`` failed deterministically
# for 15 of 17 buildcamp enrollees — not a transient fault worth retrying
# harder). The workspace join link already lives in the ``maven_welcome``
# email (``slack_join_url`` -> ``/community/slack`` -> ``SLACK_INVITE_URL``),
# so the step is repurposed to mirror ``welcome_status`` instead: it records
# that the link was (or was not) delivered rather than attempting a direct
# invite. These three notes are rendered verbatim on
# /studio/maven-events/<pk>/, so a person debugging "why didn't they reach
# Slack?" reaches the right conclusion — check the welcome email, not a
# Slack API error — from the page alone.
SLACK_JOIN_LINK_DELIVERED_NOTE = (
    "Join link delivered via the maven_welcome email; direct Slack invite "
    "is not attempted for Maven enrollees."
)
SLACK_JOIN_LINK_SUPPRESSED_NOTE = (
    "Join link not delivered: welcome email suppressed by the enrollee's "
    "maven_emails preference."
)
SLACK_JOIN_LINK_WELCOME_FAILED_NOTE = (
    "Join link not delivered: the maven_welcome email failed to send. "
    "Retry the welcome step, then retry slack."
)


# Issue #1732: the CRM contact tags the webhook applies. ``maven`` is a
# module constant because it is true of every enrollee regardless of course;
# the per-course prefix is configuration (``MAVEN_COURSE_TAG_PREFIXES``).
# These three notes are persisted into ``tagging_error`` and rendered
# verbatim on /studio/maven-events/<pk>/, so they are registered in
# ``api.serializers.maven._SAFE_CONTROLLED_REASONS`` — the operator API
# redacts any persisted note that is not on that allowlist. Variable detail
# (which tags, which course key) goes to ``actions`` and a structured log
# line, never into the 255-character error column.
MAVEN_TAG = "maven"
MAVEN_TAGS_APPLIED_NOTE = "Maven CRM contact tags applied."
MAVEN_TAGS_NO_PREFIX_NOTE = (
    "No tag prefix configured for this course in MAVEN_COURSE_TAG_PREFIXES; "
    "applied the broad maven tag only."
)
MAVEN_TAGS_NO_USER_NOTE = (
    "No account is linked to this occurrence, so no contact tags were applied."
)


def _maven_tag_names(course_key, cohort_key):
    """Return ``(prefix, tags)`` for one occurrence's course/cohort keys.

    ``tags`` always starts with the broad ``maven`` tag. A configured course
    prefix adds ``<prefix>`` and, when the cohort key contributes anything of
    its own, ``<prefix>-<cohort_key>``. Every name is built from the stable
    ``*_key`` fields and normalized — never from the display labels, which
    Maven delivers inconsistently (``4`` and ``4/11`` for the same cohort).
    """
    tags = [MAVEN_TAG]
    prefix = normalize_tag(maven_course_tag_prefix(course_key))
    if not prefix:
        return "", tags
    tags.append(prefix)
    cohort_tag = normalize_tag(f"{prefix}-{cohort_key or ''}")
    if cohort_tag and cohort_tag != prefix:
        tags.append(cohort_tag)
    return prefix, tags


def _run_tagging_step(row, actions):
    """Apply the Maven CRM contact tags; return ``(status, persisted_note)``.

    Never sends email, never writes a ``CommunityAuditLog`` row (the ledger
    step is the audit trail), and never removes a tag. ``add_tag`` is
    idempotent, so a redelivery cannot duplicate anything.
    """
    if row.user_id is None:
        return MavenEnrollmentEvent.STEP_SKIPPED, MAVEN_TAGS_NO_USER_NOTE
    prefix, tags = _maven_tag_names(row.course_key, row.cohort_key)
    for tag in tags:
        add_tag(row.user, tag)
    actions.append(f"Applied contact tags: {', '.join(tags)}.")
    logger.info(
        "Maven tagging applied occurrence=%s course_key=%s cohort_key=%s tags=%s",
        row.pk,
        row.course_key or "(none)",
        row.cohort_key or "(none)",
        ",".join(tags),
    )
    if not prefix:
        return MavenEnrollmentEvent.STEP_SUCCEEDED, MAVEN_TAGS_NO_PREFIX_NOTE
    return MavenEnrollmentEvent.STEP_SUCCEEDED, MAVEN_TAGS_APPLIED_NOTE


def _retract_maven_tags(row, actions):
    """Drop the buildcamp tags on removal, keeping the broad ``maven`` tag.

    Runs from the ``removal`` step regardless of ``tagging_status``: cohorts
    1-4 were tagged by an operator import outside the ledger and their
    occurrences are ``tagging_status=skipped``, yet a later removal must
    still retract. A missing user, an unmapped course, or tags that are
    already absent are no-ops rather than errors.
    """
    if row.user_id is None:
        return []
    prefix = normalize_tag(maven_course_tag_prefix(row.course_key))
    if not prefix:
        return []
    user = row.user
    retracted = []
    cohort_tag = normalize_tag(f"{prefix}-{row.cohort_key or ''}")
    if cohort_tag and cohort_tag != prefix and cohort_tag in (user.tags or []):
        remove_tag(user, cohort_tag)
        retracted.append(cohort_tag)
    # The course-level tag survives only while the member still carries some
    # other cohort under it — a returning student keeps ``ai-buildcamp``.
    if prefix in (user.tags or []) and not any(
        tag.startswith(f"{prefix}-") for tag in (user.tags or [])
    ):
        remove_tag(user, prefix)
        retracted.append(prefix)
    if retracted:
        actions.append(f"Retracted contact tags: {', '.join(retracted)}.")
    return retracted


def _staff_welcome_bcc():
    """Return the staff address that gets a hidden copy of the welcome.

    Issue #1570: Maven enrollees get the same treatment as Stripe paid
    signups (``community.services.staff_notifications.notify_paid_signup``)
    — staff receives a copy of the exact member-facing welcome, gated on the
    same ``STAFF_SIGNUP_NOTIFY_EMAIL`` setting, so an unset value is a clean
    no-op. Validated first: one malformed optional BCC makes SES reject the
    enrollee's primary To as well, and a bad staff address must never cost a
    real enrollee their welcome email.
    """
    return validate_email_config_value(
        "STAFF_SIGNUP_NOTIFY_EMAIL",
        get_config("STAFF_SIGNUP_NOTIFY_EMAIL", ""),
    ) or None


def _send_welcome(occurrence, actions):
    # A1.2 slice 3: the welcome goes through the durable package delivery.
    # The stored context carries no course identifier (issue #1682): the
    # member-facing name is the linked Course's title, attached as the
    # delivery's natural relation and re-read by the worker resolver at
    # delivery time. The worker also mints every link and token then.
    # ``sent`` means the durable delivery exists — the SES outcome and the
    # ``EmailLog`` audit row land from the worker.
    try:
        course = resolve_maven_course(occurrence.course_key)
    except MavenUnknownCourseError:
        # Same soft handling as ``_revoke_maven_grants``: an unconfigured
        # or unknown key never fails or suppresses the welcome step. The
        # mail degrades to the template's generic course-free copy.
        course = None
    delivery = send_package_mail(
        occurrence.user,
        "maven_welcome",
        _welcome_context(),
        bcc=_staff_welcome_bcc(),
        related=course,
    )
    if delivery.state == EmailDelivery.State.SUPPRESSED:
        # Honest action for the suppressed case; the welcome step itself
        # still succeeded (nothing to retry). The preference resolver never
        # suppresses this transactional purpose in practice.
        actions.append("maven_welcome suppressed by preferences; not sent.")
        return
    actions.append("Sent maven_welcome email.")


# Issue #1593: this link lives in an unsolicited welcome email that people act
# on days or weeks later, so it is not held to the 24-hour registration
# contract. That is this codebase's own distinction, not a new one:
# ``VERIFY_FOOTER_TOKEN_EXPIRY_HOURS`` in ``email_app.services.email_service``
# already gives the footer verify link 7 days "because email recipients open
# messages on their own schedule", and that token writes the same
# ``email_verified`` field through the same endpoint family. Thirty rather
# than seven because every dimension that comment cites is stronger here: the
# reader has no pending intent, the course may not have started, and there is
# no resend path for this token. Bounded rather than non-expiring, unlike the
# ``unsubscribe`` and ``maven_email_opt_out`` footer tokens, because this one
# also asserts mailbox ownership. An expired click still lands on a page that
# routes to account email preferences rather than dead-ending.
NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS = 24 * 30


def _welcome_context(course=""):
    """Durable send context: no course identifier since issue #1682.

    The production welcome (``_send_welcome``) persists ``course_name``
    empty: Maven's raw course label and cohort label are integration
    identifiers, not display copy, so the member-facing name is resolved
    at delivery time from the delivery's course relation —
    the linked course's title, else this empty scalar, which renders the
    template's generic course-free copy.

    There is deliberately no ``cohort`` parameter. Before #1682 this
    helper fell back to the Maven cohort label when the course label was
    empty, which is how "You're enrolled in Cohort 1" reached enrollees.
    Re-wiring the producer to pass a cohort now raises instead of
    shipping an integration identifier as display copy. The ``course``
    parameter is the legacy scalar shape only: relation-less deliveries
    (rows queued before #1682) keep rendering whatever scalar was stored
    at queue time.

    No ``user_name`` key here on purpose (issue #1591): the worker resolver
    resolves the greeting with ``greeting_name`` so a nameless enrollee gets
    "Hi there," and not their email handle. Every link and token is minted
    by ``email_app.hooks._resolve_maven_welcome_context`` at delivery time,
    which also starts the token expiry clocks then — a worker backlog or a
    retry loop never shortens the recipient's usable window (#1593). No
    placeholder ever reaches an enrollee: a resolved course title, else the
    template's generic, course-free copy.
    """
    return {
        "course_name": (course or "").strip(),
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
    # Issue #1732: CRM tagging runs first and gates nothing. The tags record
    # that this person arrived via Maven, which stays true whether or not the
    # entitlement lands, so a failed ``tagging`` step must never stop
    # ``override`` or anything after it.
    _run_step(occurrence.pk, "tagging", actions, force=force)
    # Access is the durable core. Do not send visible onboarding actions until
    # it has succeeded; concurrent duplicate deliveries will observe RUNNING
    # and leave those later steps for the winning worker.
    _run_step(occurrence.pk, "override", actions, force=force)
    occurrence.refresh_from_db(fields=["override_status"])
    if occurrence.override_status != MavenEnrollmentEvent.STEP_SUCCEEDED:
        return actions
    # A failed or still-pending ``enrollment`` never blocks the other three —
    # a course grant is independent of Slack/welcome eligibility. ``welcome``
    # runs before ``slack`` (#1665): the ``slack`` step now mirrors
    # ``welcome_status`` rather than calling the Slack API, so the welcome
    # outcome must exist before ``slack`` is evaluated.
    for name in ("enrollment", "notification", "welcome", "slack"):
        _run_step(occurrence.pk, name, actions, force=force)
    return actions


def retry_occurrence_step(occurrence, step):
    """Force one incomplete step and return its truthful persisted outcome.

    The claim, attempt increment, provider call, and finish transition remain
    owned by ``_run_step``. A successful override also resumes currently
    eligible downstream enrollment steps through the ordinary capped runner.
    """
    if step not in STEP_NAMES:
        raise ValueError("unknown Maven step")

    actions = []
    result = _run_step(occurrence.pk, step, actions, force=True)
    if (
        result.attempted
        and step == "override"
        and result.outcome == MavenEnrollmentEvent.STEP_SUCCEEDED
    ):
        occurrence.refresh_from_db()
        run_occurrence_steps(occurrence)
    return result


def _run_step(pk, name, actions, *, force=False, send_student_email=True):
    if name not in STEP_NAMES:
        raise ValueError("unknown Maven step")
    status_field = f"{name}_status"
    attempts_field = f"{name}_attempts"
    attempted_field = f"{name}_attempted_at"
    completed_field = f"{name}_completed_at"
    error_field = f"{name}_error"
    with transaction.atomic():
        # ``of=("self",)`` locks the occurrence row only. ``user`` is a
        # nullable FK, so ``select_related("user")`` compiles to a LEFT OUTER
        # JOIN and a bare ``FOR UPDATE`` makes PostgreSQL raise
        # NotSupportedError ("FOR UPDATE cannot be applied to the nullable
        # side of an outer join") on every call — outside the try below, so it
        # escaped the view as an HTML 500 instead of a recorded step failure.
        # SQLite reports has_select_for_update=False and drops the clause, so
        # the whole flow only ever worked there. The step lease belongs to the
        # occurrence anyway; a webhook must not hold a lock on a User row.
        row = (
            MavenEnrollmentEvent.objects.select_for_update(of=("self",))
            .select_related("user")
            .get(pk=pk)
        )
        status = getattr(row, status_field)
        attempts = getattr(row, attempts_field)
        if status == row.STEP_SUCCEEDED:
            return MavenStepRetryResult(
                step=name,
                outcome=status,
                attempted=False,
                reason="not_retryable",
            )
        if status == row.STEP_SKIPPED and not (
            force
            and (
                name == "enrollment"
                # Issue #1732: ``tagging_status`` defaults to ``skipped`` for
                # every occurrence that pre-dates the step, so an operator must
                # be able to force it on a still-active occurrence. A removed
                # occurrence declines — re-tagging someone who was removed
                # would undo the retraction the removal step performed.
                or (
                    name == "tagging"
                    and row.lifecycle == MavenEnrollmentEvent.LIFECYCLE_ACTIVE
                )
            )
        ):
            # A skipped step is normally terminal: re-running a
            # preference-suppressed welcome would re-send member-visible
            # email, and a not-in-workspace slack re-lookup only repeats
            # itself. The one recoverable case is an enrollment step that
            # was skipped because course.yaml declared no matching
            # maven_course_key / cohort key at the time: the grant is
            # idempotent and member-invisible, and force-retrying it is the
            # documented roster-replay path (website #1662 go-live
            # checklist step 10; #1659 promised retryability).
            return MavenStepRetryResult(
                step=name,
                outcome=status,
                attempted=False,
                reason="not_retryable",
            )
        if status == row.STEP_RUNNING:
            attempted_at = getattr(row, attempted_field)
            if attempted_at and attempted_at > timezone.now() - RUNNING_STEP_LEASE:
                actions.append(f"{name.title()} is already running; not repeated.")
                return MavenStepRetryResult(
                    step=name,
                    outcome=status,
                    attempted=False,
                    reason="in_progress",
                )
        if name == "slack" and row.welcome_status in (
            row.STEP_PENDING, row.STEP_RUNNING,
        ):
            # Issue #1665: ``slack`` mirrors ``welcome_status`` instead of
            # calling the Slack API, and the ordinary occurrence loop always
            # runs ``welcome`` first — so this is reachable only via a
            # standalone Studio/API retry of ``slack`` issued before
            # ``welcome`` has ever resolved. There is nothing to mirror yet,
            # so the step declines the attempt entirely: no attempt is
            # consumed (even under a forced retry) and ``slack_status`` is
            # left untouched.
            actions.append(
                "Slack step deferred: the welcome step has not resolved yet."
            )
            return MavenStepRetryResult(
                step=name,
                outcome=status,
                attempted=False,
                reason="welcome_pending",
            )
        if attempts >= MAX_STEP_ATTEMPTS and not force:
            actions.append(f"{name.title()} retry limit reached.")
            return MavenStepRetryResult(
                step=name,
                outcome=status,
                attempted=False,
                reason="attempt_limit",
            )
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
        if name == "tagging":
            tagging_status, tagging_note = _run_tagging_step(row, actions)
            _finish_step(pk, name, tagging_status, tagging_note)
            return MavenStepRetryResult(
                step=name,
                outcome=tagging_status,
                attempted=True,
            )
        elif name == "override":
            tier = Tier.objects.get(slug=maven_override_tier_slug())
            expiry = row.created_at + timedelta(days=maven_override_duration_days())
            actions.append(_grant_or_refresh_override(row.user, tier, expiry, row.cohort, row.course, source=f"maven:{row.identity_hash}"))
        elif name == "enrollment":
            _run_enrollment_step(row, actions)
        elif name == "notification":
            from community.services.staff_notifications import notify_maven_enrollment

            tier, entitlement_expiry = _enrollment_notification_entitlement(row)
            delivered = notify_maven_enrollment(
                row.user,
                occurrence_id=row.pk,
                account_created=row.account_created,
                course=row.course,
                cohort=row.cohort,
                tier=tier,
                entitlement_expiry=entitlement_expiry,
            )
            if not delivered:
                _finish_step(pk, name, MavenEnrollmentEvent.STEP_SKIPPED, "")
                actions.append("Staff enrollment heads-up skipped: no usable destination.")
                return MavenStepRetryResult(
                    step=name,
                    outcome=MavenEnrollmentEvent.STEP_SKIPPED,
                    attempted=True,
                )
            actions.append("Sent staff enrollment heads-up.")
        elif name == "slack":
            # Issue #1665: no Slack API call. The step mirrors the
            # just-resolved ``welcome_status`` — reached here only once it is
            # terminal (see the early return above) — because the join link
            # lives in the welcome email rather than a direct invite.
            if row.welcome_status == MavenEnrollmentEvent.STEP_SUCCEEDED:
                slack_status, slack_note = (
                    MavenEnrollmentEvent.STEP_SUCCEEDED,
                    SLACK_JOIN_LINK_DELIVERED_NOTE,
                )
                actions.append("Join link delivered via the maven_welcome email.")
            elif row.welcome_status == MavenEnrollmentEvent.STEP_SKIPPED:
                slack_status, slack_note = (
                    MavenEnrollmentEvent.STEP_SKIPPED,
                    SLACK_JOIN_LINK_SUPPRESSED_NOTE,
                )
                actions.append(
                    "Join link not delivered: welcome email suppressed by preference."
                )
            else:
                slack_status, slack_note = (
                    MavenEnrollmentEvent.STEP_FAILED,
                    SLACK_JOIN_LINK_WELCOME_FAILED_NOTE,
                )
                actions.append(
                    "Join link not delivered: the welcome email failed to send."
                )
            _finish_step(pk, name, slack_status, slack_note)
            return MavenStepRetryResult(
                step=name,
                outcome=slack_status,
                attempted=True,
            )
        elif name == "welcome":
            if not row.user.email_preferences.get("maven_emails", True):
                _finish_step(pk, name, MavenEnrollmentEvent.STEP_SKIPPED, "")
                actions.append("Maven welcome suppressed by scoped preference.")
                return MavenStepRetryResult(
                    step=name,
                    outcome=MavenEnrollmentEvent.STEP_SKIPPED,
                    attempted=True,
                )
            _send_welcome(row, actions)
        elif name == "removal":
            _run_removal_step(row, actions, send_student_email=send_student_email)
    except Exception as exc:
        # Provider exception messages can contain addresses, response bodies,
        # or tokens. Persist and log only the safe exception class.
        logger.warning(
            "Maven %s step failed for occurrence=%s error_class=%s",
            name,
            pk,
            exc.__class__.__name__,
        )
        _finish_step(pk, name, MavenEnrollmentEvent.STEP_FAILED, _safe_error(exc))
        actions.append(f"{name.title()} failed; persisted for retry.")
        return MavenStepRetryResult(
            step=name,
            outcome=MavenEnrollmentEvent.STEP_FAILED,
            attempted=True,
        )
    else:
        _finish_step(pk, name, MavenEnrollmentEvent.STEP_SUCCEEDED, "")
        return MavenStepRetryResult(
            step=name,
            outcome=MavenEnrollmentEvent.STEP_SUCCEEDED,
            attempted=True,
        )


def _enrollment_notification_entitlement(occurrence):
    """Return the applied Maven tier and the member's retained access expiry."""
    now = timezone.now()
    grant = (
        TierOverride.objects.filter(
            user=occurrence.user,
            source=f"maven:{occurrence.identity_hash}",
        )
        .select_related("override_tier")
        .first()
    )
    if grant is None:
        tier = Tier.objects.get(slug=maven_override_tier_slug())
    else:
        tier = grant.override_tier

    expiry_candidates = list(
        TierOverride.objects.filter(
            user=occurrence.user,
            is_active=True,
            expires_at__gt=now,
            override_tier__level__gte=tier.level,
        ).values_list("expires_at", flat=True)
    )
    # Issue #1579: tier/billing state lives on payments.Membership.
    user_membership = occurrence.user.membership
    if (
        user_membership.tier_id
        and user_membership.tier.level >= tier.level
        and user_membership.billing_period_end
        and user_membership.billing_period_end > now
    ):
        expiry_candidates.append(user_membership.billing_period_end)
    if grant is not None and grant.expires_at not in expiry_candidates:
        expiry_candidates.append(grant.expires_at)
    return tier, max(expiry_candidates)


def _cohort_event_series(cohort):
    """Return the cohort's linked ``EventSeries``, or ``None``.

    Reads ``event_series`` defensively (issue #1660 adds the field to
    ``Cohort`` on a parallel track): ``getattr`` with a default so this
    ships and stays a clean no-op whether or not that field has landed yet.
    """
    if not getattr(cohort, "event_series_id", None):
        return None
    return getattr(cohort, "event_series", None)


def _run_enrollment_step(row, actions):
    """Resolve course/cohort and grant course access + cohort membership.

    Idempotent: ``get_or_create`` never duplicates an existing
    ``CourseAccess``/``CohortEnrollment`` row. An unresolvable
    ``course_key``/``cohort_key`` raises one of the dedicated
    ``MavenUnknown*Error`` classes so ``_run_step`` persists a safe,
    un-redacted, retryable failure rather than silently skipping.
    """
    course = resolve_maven_course(row.course_key)
    cohort = resolve_maven_cohort(course, row.cohort_key)

    CourseAccess.objects.get_or_create(
        user=row.user,
        course=course,
        defaults={"access_type": "granted"},
    )
    actions.append(f"Granted course access to {course.slug}.")

    CohortEnrollment.objects.get_or_create(cohort=cohort, user=row.user)
    actions.append(f"Enrolled in cohort {cohort.external_key}.")
    added_tags = apply_cohort_enrollment_tags(row.user, cohort)
    if added_tags:
        actions.append(f"Applied cohort contact tags: {', '.join(added_tags)}.")

    series = _cohort_event_series(cohort)
    if series is not None:
        from events.services.registration import get_or_create_series_registration

        get_or_create_series_registration(series, row.user)
        actions.append("Registered for the cohort's office-hours series.")


def _revoke_maven_grants(row, actions, outcome=None, *, transfer=None):
    """Best-effort revoke of the course grant and cohort membership on removal.

    Resolution mirrors the ``enrollment`` step, but an unresolvable
    course/cohort key never fails the ``removal`` step: an occurrence whose
    ``enrollment`` step never succeeded (unconfigured key, or a
    pre-#1659 backfilled row) has nothing to revoke, and removal's staff
    heads-up must keep working regardless.

    ``CohortEnrollment`` is deleted unconditionally for the resolved
    cohort. ``CourseAccess(access_type="granted")`` is deleted unless
    ``transfer`` is set: another active occurrence of a DIFFERENT cohort of
    the same course (see :func:`_other_active_enrollments`), so a stale row
    for the same cohort under Maven's old keys never keeps access.
    ``access_type="purchased"`` rows are never touched.

    ``outcome`` (a :class:`RemovalOutcome`) records what actually changed so
    the staff summary reports real results rather than static text.
    """
    outcome = outcome if outcome is not None else RemovalOutcome()
    if row.user_id is None:
        return
    try:
        course = resolve_maven_course(row.course_key)
    except MavenUnknownCourseError:
        outcome.course_access = (
            "Course access unchanged: this Maven course isn't linked to a course here."
        )
        return
    cohort = None
    try:
        cohort = resolve_maven_cohort(course, row.cohort_key)
    except MavenUnknownCohortError:
        cohort = None

    if cohort is not None:
        deleted, _counts = CohortEnrollment.objects.filter(
            cohort=cohort, user_id=row.user_id,
        ).delete()
        if deleted:
            actions.append("Revoked cohort enrollment.")
            outcome.cohort_enrollment = f"Removed from cohort {cohort.external_key}."
            # CRM timeline row only: the removal step already sends the
            # Maven removal staff heads-up, so no second Slack post.
            record_unenrollment(
                row.user, course, cohort=cohort,
                cause=UNENROLL_CAUSE_ACCESS_LOST, notify=False,
            )
        series = _cohort_event_series(cohort)
        if series is not None:
            from events.models import SeriesRegistration

            series_deleted, _series_counts = SeriesRegistration.objects.filter(
                series=series, user_id=row.user_id,
            ).delete()
            if series_deleted:
                actions.append("Revoked standing series registration.")
                outcome.series_registration = "Removed from the cohort's event series."

    if transfer is not None:
        outcome.course_access = (
            f"Kept course access: still in cohort {_occurrence_label(transfer)}."
        )
        return
    deleted, _counts = CourseAccess.objects.filter(
        user_id=row.user_id, course=course, access_type="granted",
    ).delete()
    if deleted:
        actions.append("Revoked course access.")
        outcome.course_access = f"Revoked access to {course.title}."
    elif CourseAccess.objects.filter(user_id=row.user_id, course=course).exists():
        outcome.course_access = "Kept course access: they bought the course directly."


@dataclass
class RemovalOutcome:
    """What the ``removal`` step actually changed, one short sentence per area.

    Every field stays empty unless something changed or was deliberately
    kept, so the staff summary lists only real outcomes, never "nothing to
    revoke" filler.
    """

    course_access: str | None = None
    cohort_enrollment: str | None = None
    series_registration: str | None = None
    tags: str | None = None
    override: list[str] = field(default_factory=list)
    student_email: str | None = None

    def summary_lines(self):
        lines = [
            line
            for line in (
                self.course_access,
                self.cohort_enrollment,
                self.series_registration,
                self.tags,
                *self.override,
                self.student_email,
            )
            if line
        ]
        return lines or [NOTHING_CHANGED_LINE]

    def as_context(self):
        return {"summary_lines": self.summary_lines()}


NOTHING_CHANGED_LINE = "Nothing needed changing: they had no access left."


def _occurrence_label(occurrence):
    return occurrence.cohort or occurrence.cohort_key or f"#{occurrence.pk}"


def _other_active_enrollments(row):
    """Split this member's other active Maven occurrences by course.

    Returns ``(same_course, other_course)``: active occurrences of a
    different cohort of the same course (a transfer), and of a different
    course. Both sides resolve through ``maven_matching`` signatures, so a
    stale row for the SAME enrollment under Maven's old identifiers (cohort
    ``4/11`` vs ``4``, course title vs slug) created before this removal
    counts as neither. A later re-enrollment of the same cohort is real and
    counts as same-course.
    """
    if row.user_id is None:
        return [], []
    own = occurrence_signature(row)
    before = row.removed_at or row.created_at
    same_course, other_course = [], []
    others = MavenEnrollmentEvent.objects.filter(
        user_id=row.user_id,
        lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
    ).exclude(pk=row.pk).order_by("-created_at", "-pk")
    for other in others:
        signature = occurrence_signature(other)
        if same_enrollment(own, signature) and other.created_at < before:
            continue
        if own[0] & signature[0]:
            same_course.append(other)
        else:
            other_course.append(other)
    return same_course, other_course


def _removed_cohort(row):
    try:
        course = resolve_maven_course(row.course_key)
        return resolve_maven_cohort(course, row.cohort_key)
    except (MavenUnknownCourseError, MavenUnknownCohortError):
        return None


def _previous_cohort_enrollment(row):
    """A ``CohortEnrollment`` in a cohort that started before the removed one.

    A returning alumnus (cohort 2, then cohort 4) keeps main access. Only an
    earlier ``start_date`` counts, in the same or another course; a later or
    parallel cohort (a transfer) gets its own override from its enrollment
    step instead. Without a dated removed cohort there is nothing to compare
    against, so nothing counts.
    """
    removed = _removed_cohort(row)
    if removed is None or removed.start_date is None:
        return None
    return (
        CohortEnrollment.objects.filter(
            user_id=row.user_id, cohort__start_date__lt=removed.start_date,
        )
        .exclude(cohort_id=removed.pk)
        .select_related("cohort__course")
        .order_by("-cohort__start_date", "-pk")
        .first()
    )


def _cohort_phrase(cohort):
    """``cohort 2 of AI Engineering Buildcamp`` from a ``Cohort`` row."""
    name = (cohort.name or cohort.external_key or f"#{cohort.pk}").strip()
    if name.lower().startswith("cohort"):
        name = name[len("cohort"):].strip()
    return f"cohort {name} of {cohort.course.title}"


def _revoke_maven_override(row, actions, *, other_course=()):
    """Revoke the Maven-granted tier override; return the staff summary lines.

    Only rows whose ``source`` starts with ``maven:`` are Maven grants. A
    staff-granted row (``source`` ``staff``, or any row with ``granted_by``)
    is kept and named. A sourceless row with no granter could be a pre-source
    Maven grant or an old manual one, so it is kept and flagged for review
    rather than guessed at. Paid Stripe tiers live on ``Membership`` and are
    never touched. Revoked rows go through the shared
    ``payments.services.tier_override_revoke`` service (same audit trail as
    Studio and the API). No active override means no line at all.

    The override is kept when the member is active in a different Maven
    course (``other_course``) or was enrolled in a cohort of any course that
    started before the removed one (a returning alumnus). A later or
    parallel cohort of the same course does not keep it.
    """
    if row.user_id is None:
        return []
    overrides = active_overrides_for(row.user)
    if not overrides:
        return []
    if not maven_removal_revokes_override():
        return [
            f"Kept {_tier_names(overrides)} access: "
            "MAVEN_REMOVAL_REVOKES_OVERRIDE is off."
        ]
    if other_course:
        other = other_course[0]
        return [
            f"Kept {_tier_names(overrides)} access: still enrolled in "
            f"{other.course or other.course_key} on Maven."
        ]
    alumnus = _previous_cohort_enrollment(row)
    if alumnus is not None:
        return [
            f"Kept {_tier_names(overrides)} access: was in "
            f"{_cohort_phrase(alumnus.cohort)}."
        ]
    maven = [o for o in overrides if (o.source or "").startswith("maven:")]
    unclear = [o for o in overrides if not o.source and o.granted_by_id is None]
    manual = [o for o in overrides if o not in maven and o not in unclear]
    for override in maven:
        revoke_tier_override(
            override, actor=f"actor=maven_removal occurrence={row.pk}",
        )
    lines = []
    if maven:
        plural = "s" if len(maven) > 1 else ""
        lines.append(
            f"Revoked the {_tier_names(maven)} membership override{plural}."
        )
        actions.append(
            f"Revoked Maven tier override(s): {', '.join(str(o.pk) for o in maven)}."
        )
    if manual:
        lines.append(
            f"Kept {_tier_names(manual)} access: the override was granted manually."
        )
    if unclear:
        lines.append(
            f"Kept {_tier_names(unclear)} access: the override has no recorded "
            "source, so check it in Studio."
        )
    return lines


def _tier_names(overrides):
    return " and ".join(sorted({o.override_tier.name.lower() for o in overrides}))


def _send_removal_student_email(row, actions, course, *, send_student_email, transfer=None):
    """Email the removed student; return the staff summary line or ``None``.

    ``None`` when there is nothing worth telling staff: no account, or a
    re-apply that deliberately skipped the email.
    """
    if row.user_id is None or not send_student_email:
        return None
    if not maven_removal_student_email_enabled():
        return "Didn't email them: MAVEN_REMOVAL_STUDENT_EMAIL is off."
    if transfer is not None:
        return f"Didn't email them: still in cohort {_occurrence_label(transfer)}."
    skip = email_skip_status(row.user, True)
    if skip:
        reason = skip.removeprefix("skipped_").replace("_", " ")
        return f"Didn't email them: {reason}."
    delivery = send_package_mail(
        row.user,
        "maven_removal",
        {
            "course_name": "",
            "membership_ended": get_user_level(row.user) < LEVEL_MAIN,
        },
        related=course,
        idempotency_key=f"maven_removal:{row.pk}",
    )
    if delivery.state == EmailDelivery.State.SUPPRESSED:
        return "Didn't email them: they turned these emails off."
    actions.append("Sent maven_removal email to the student.")
    return "Sent them the removal email."


def _run_removal_step(row, actions, *, send_student_email=True):
    """Apply every automatic removal change, then tell staff what happened.

    Order matters: course access, cohort enrollment, series registration,
    tags and the Maven override are all changed first, the student email is
    queued, and only then does the staff summary go out, built from the real
    outcomes. Every change is idempotent, so a replay or re-apply is safe.
    """
    from community.services.staff_notifications import (  # noqa: PLC0415 -- same lazy edge as the notification step above
        notify_maven_cohort_removal,
    )

    outcome = RemovalOutcome()
    course = None
    same_course, other_course = _other_active_enrollments(row)
    transfer = same_course[0] if same_course else None
    if row.user_id is not None:
        try:
            course = resolve_maven_course(row.course_key)
        except MavenUnknownCourseError:
            course = None
        _revoke_maven_grants(row, actions, outcome, transfer=transfer)
        retracted = _retract_maven_tags(row, actions)
        if retracted:
            noun = "tags" if len(retracted) > 1 else "tag"
            outcome.tags = f"Removed {noun} {', '.join(retracted)}."
        outcome.override = _revoke_maven_override(
            row, actions, other_course=other_course,
        )
    outcome.student_email = _send_removal_student_email(
        row, actions, course,
        send_student_email=send_student_email, transfer=transfer,
    )
    notify_maven_cohort_removal(
        row.user, row.cohort, row.course, email=row.email,
        outcome=outcome.as_context(),
    )
    actions.append("Sent staff removal summary.")
    return outcome


def reapply_removal(occurrence, *, send_student_email=False):
    """Re-run the ``removal`` step for an already-removed occurrence.

    For removals processed before the step revoked overrides and sent the
    real-outcome staff summary. Every change is idempotent; the staff summary
    is sent again with the corrected outcome. The student email is sent only
    when ``send_student_email`` is true (its per-occurrence idempotency key
    still prevents a second copy).
    """
    if occurrence.lifecycle != MavenEnrollmentEvent.LIFECYCLE_REMOVED:
        raise ValueError("occurrence is not removed")
    actions = []
    # Repair a split pair first: an earlier active occurrence of the same
    # enrollment would otherwise count as "still active" and keep access.
    close_split_siblings(occurrence, actions)
    MavenEnrollmentEvent.objects.filter(pk=occurrence.pk).update(
        removal_status=MavenEnrollmentEvent.STEP_PENDING,
        updated_at=timezone.now(),
    )
    result = _run_step(
        occurrence.pk, "removal", actions,
        force=True, send_student_email=send_student_email,
    )
    return result, actions


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
