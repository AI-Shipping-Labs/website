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

from accounts.models import TierOverride
from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from community.models import CommunityAuditLog
from content.access import LEVEL_MAIN, get_user_level
from content.models import Course, CourseAccess
from content.models.cohort import Cohort, CohortEnrollment
from email_app.package_mail import send_package_mail
from integrations.config import get_config, validate_email_config_value
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
MAX_STEP_ATTEMPTS = 3
MAX_DATABASE_CONTENTION_RETRIES = 10
RUNNING_STEP_LEASE = timedelta(minutes=15)
STEP_NAMES = ("override", "enrollment", "notification", "slack", "welcome", "removal")
_SQLITE_DELIVERY_LOCK = threading.Lock()


class MavenTransientError(Exception):
    """The durable core entitlement step failed and the sender should retry."""


class MavenUnknownCourseError(Exception):
    """No ``Course.maven_course_key`` matches the occurrence's ``course_key``."""


class MavenUnknownCohortError(Exception):
    """No ``Cohort.external_key`` under the resolved course matches ``cohort_key``."""


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
        return MavenResult("removal_notified", actions=["Would close the active occurrence and revoke its course access and cohort membership (tier override and Slack membership are never touched).", "Would persist and attempt the independent removal notification step."], user_id=user.pk if user else None)
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
        for name in ("override", "enrollment", "notification", "slack", "welcome")
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
        signup_source="imported", unsubscribed=True,
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


# Ledger note recorded on the Maven ``slack`` step when the enrollee is not
# in the Slack workspace. Rendered verbatim on /studio/maven-events/<pk>/ so
# a person debugging "why did they never reach Slack?" reaches the right
# conclusion from the page alone.
SLACK_NOT_IN_WORKSPACE_NOTE = (
    "Enrollee is not in the Slack workspace; the join link was delivered in "
    "the welcome email."
)

# Ledger note when the enrollee IS in the workspace but joined no community
# channel. Almost always an operator-fixable Slack configuration problem
# (bot not in the channel, wrong channel id), so it is a retryable failure
# rather than a silent success.
SLACK_CHANNEL_JOIN_FAILED_NOTE = (
    "Enrollee is in the Slack workspace but joined no community channel:"
)


def _invite_to_slack(user, actions):
    """Add the enrollee to Slack and report what actually happened.

    Maven enrollees get exactly one email (``maven_welcome``), which now
    carries the Slack join link, so the generic ``community_invite`` is
    suppressed here.

    Returns ``(step_status, note)``. ``succeeded`` requires that the member
    actually joined at least one community channel — never merely that a
    Slack user id resolved. When every channel add errored (the bot is not
    in the channel, a channel id is wrong, or the bounded rate-limit retry
    is exhausted) the step is ``failed`` with the channel errors in the
    note, so it is visible in Studio and retried rather than recorded as a
    delivery that never happened.
    """
    from community.services.slack import (
        INVITE_ADDED_TO_CHANNELS,
        INVITE_CHANNEL_JOIN_FAILED,
        get_community_service,
    )

    result = get_community_service().invite(user, send_invite_email=False)
    if result.outcome == INVITE_ADDED_TO_CHANNELS:
        actions.append("Added to Slack community channels.")
        return MavenEnrollmentEvent.STEP_SUCCEEDED, ""
    if result.outcome == INVITE_CHANNEL_JOIN_FAILED:
        note = f"{SLACK_CHANNEL_JOIN_FAILED_NOTE} {result.detail}".strip()
        actions.append("Slack channel join failed; persisted for retry.")
        return MavenEnrollmentEvent.STEP_FAILED, note[:255]
    actions.append(
        "Not in the Slack workspace; join link delivered in the welcome email."
    )
    return MavenEnrollmentEvent.STEP_SKIPPED, SLACK_NOT_IN_WORKSPACE_NOTE


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


def _send_welcome(user, course, cohort, actions):
    # A1.2 slice 3: the welcome goes through the durable package delivery.
    # The stored context carries the course scalar only (#1613); the worker
    # resolver mints every link and token at delivery time. ``sent`` means
    # the durable delivery exists — the SES outcome and the ``EmailLog``
    # audit row land from the worker.
    delivery = send_package_mail(
        user,
        "maven_welcome",
        _welcome_context(course, cohort),
        bcc=_staff_welcome_bcc(),
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


def _welcome_context(course, cohort=""):
    """Durable send context: the course scalar only (issues #1613, #1647).

    No ``user_name`` key here on purpose (issue #1591): the worker resolver
    resolves the greeting with ``greeting_name`` so a nameless enrollee gets
    "Hi there," and not their email handle. Every link and token is minted
    by ``email_app.hooks._resolve_maven_welcome_context`` at delivery time,
    which also starts the token expiry clocks then — a worker backlog or a
    retry loop never shortens the recipient's usable window (#1593). No
    placeholder ever reaches an enrollee: course, else cohort, else the
    template's generic, course-free copy.
    """
    return {
        "course_name": (course or "").strip() or (cohort or "").strip(),
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
    # A failed or still-pending ``enrollment`` never blocks the other three —
    # a course grant is independent of Slack/welcome eligibility.
    for name in ("enrollment", "notification", "slack", "welcome"):
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


def _run_step(pk, name, actions, *, force=False):
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
            force and name == "enrollment"
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
        if name == "override":
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
            # ``skipped`` is "we correctly did nothing" — never ``succeeded``
            # (nothing happened) and never ``failed`` (nothing went wrong, and
            # a retry would only burn an attempt). ``failed`` is reserved for
            # a real, retryable Slack problem.
            slack_status, slack_note = _invite_to_slack(row.user, actions)
            if slack_status != MavenEnrollmentEvent.STEP_SUCCEEDED:
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
            _send_welcome(row.user, row.course, row.cohort, actions)
        elif name == "removal":
            from community.services.staff_notifications import notify_maven_cohort_removal
            notify_maven_cohort_removal(row.user, row.cohort, row.course, email=row.email)
            actions.append("Sent staff removal heads-up.")
            _revoke_maven_grants(row, actions)
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


def resolve_maven_course(course_key):
    """Resolve ``Course.maven_course_key`` case-insensitively, or raise.

    A blank ``course_key`` is always unresolvable — matching it against
    blank ``maven_course_key`` rows (the common unconfigured default) would
    silently grant the wrong course, so it is excluded explicitly rather
    than relying on an empty-string match to fail to find anything.
    """
    if not course_key:
        raise MavenUnknownCourseError("course_key is blank")
    course = (
        Course.objects.exclude(maven_course_key="")
        .filter(maven_course_key__iexact=course_key)
        .first()
    )
    if course is None:
        raise MavenUnknownCourseError(
            f"no Course.maven_course_key matches {course_key!r}"
        )
    return course


def resolve_maven_cohort(course, cohort_key):
    """Resolve ``Cohort.external_key`` under ``course`` case-insensitively, or raise."""
    if not cohort_key:
        raise MavenUnknownCohortError("cohort_key is blank")
    cohort = (
        Cohort.objects.filter(course=course)
        .exclude(external_key="")
        .filter(external_key__iexact=cohort_key)
        .first()
    )
    if cohort is None:
        raise MavenUnknownCohortError(
            f"no Cohort.external_key under course={course.pk} matches {cohort_key!r}"
        )
    return cohort


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

    series = _cohort_event_series(cohort)
    if series is not None:
        from events.services.registration import get_or_create_series_registration

        get_or_create_series_registration(series, row.user)
        actions.append("Registered for the cohort's office-hours series.")


def _revoke_maven_grants(row, actions):
    """Best-effort revoke of the course grant and cohort membership on removal.

    Resolution mirrors the ``enrollment`` step, but an unresolvable
    course/cohort key never fails the ``removal`` step: an occurrence whose
    ``enrollment`` step never succeeded (unconfigured key, or a
    pre-#1659 backfilled row) has nothing to revoke, and removal's staff
    heads-up must keep working regardless.

    ``CohortEnrollment`` is deleted unconditionally for the resolved
    cohort. ``CourseAccess(access_type="granted")`` is deleted only when no
    other ``lifecycle=active`` occurrence for this user still grants the
    same resolved course — a member holding a second active cohort under
    the same course keeps access. ``access_type="purchased"`` rows are
    never touched.
    """
    if row.user_id is None:
        return
    try:
        course = resolve_maven_course(row.course_key)
    except MavenUnknownCourseError:
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
        series = _cohort_event_series(cohort)
        if series is not None:
            from events.models import SeriesRegistration

            series_deleted, _series_counts = SeriesRegistration.objects.filter(
                series=series, user_id=row.user_id,
            ).delete()
            if series_deleted:
                actions.append("Revoked standing series registration.")

    other_active = MavenEnrollmentEvent.objects.filter(
        user_id=row.user_id,
        lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
        course_key__iexact=course.maven_course_key,
    ).exists()
    if not other_active:
        deleted, _counts = CourseAccess.objects.filter(
            user_id=row.user_id, course=course, access_type="granted",
        ).delete()
        if deleted:
            actions.append("Revoked course access.")


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
