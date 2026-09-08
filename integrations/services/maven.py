"""Consent-aware, occurrence-based Maven enrollment processing (issue #960)."""

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone

from accounts.models import TierOverride
from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from accounts.utils.tokens import generate_password_reset_token, generate_user_action_token
from community.models import CommunityAuditLog
from content.access import LEVEL_MAIN, get_user_level
from email_app.services import EmailService
from integrations.config import get_config, site_base_url, validate_email_config_value
from integrations.maven_config import (
    maven_course_slack_channel,
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
STEP_NAMES = ("override", "notification", "slack", "welcome", "removal")
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
    if occurrence.override_status == MavenEnrollmentEvent.STEP_FAILED:
        raise MavenTransientError("maven entitlement step failed")
    status = "already_member" if not occurrence.welcome_eligible else "onboarded"
    if not created_occurrence and all(
        getattr(occurrence, f"{name}_status") in {MavenEnrollmentEvent.STEP_SUCCEEDED, MavenEnrollmentEvent.STEP_SKIPPED}
        for name in ("override", "notification", "slack", "welcome")
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
    EmailService().send(
        user,
        "maven_welcome",
        _welcome_context(user, course, cohort),
        bcc=_staff_welcome_bcc(),
    )
    actions.append("Sent maven_welcome email.")


# Issue #1593: this link lives in an unsolicited welcome email that people act
# on days or weeks later, so it is not held to the 24-hour registration
# contract. That is this codebase's own distinction, not a new one:
# ``EmailService.VERIFY_FOOTER_TOKEN_EXPIRY_HOURS`` already gives the footer
# verify link 7 days "because email recipients open messages on their own
# schedule", and that token writes the same ``email_verified`` field through
# the same endpoint family. Thirty rather than seven because every dimension
# that comment cites is stronger here: the reader has no pending intent, the
# course may not have started, and there is no resend path for this token.
# Bounded rather than non-expiring, unlike the ``unsubscribe`` and
# ``maven_email_opt_out`` footer tokens, because this one also asserts mailbox
# ownership. An expired click still lands on a page that routes to account
# email preferences rather than dead-ending.
NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS = 24 * 30


def _welcome_context(user, course, cohort=""):
    site_url = site_base_url().rstrip("/")
    reset_token = generate_password_reset_token(user, expiry_hours=24)
    opt_out_token = generate_user_action_token(user.pk, "maven_email_opt_out")
    opt_in_token = generate_user_action_token(
        user.pk,
        "verify_and_subscribe",
        expiry_hours=NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
    )
    return {
        # No "user_name" key here on purpose (issue #1591): caller context
        # wins over EmailService's injected default, and Maven enrollees
        # regularly arrive with no name at all. Let EmailService resolve
        # the greeting so a nameless enrollee gets "Hi there," and not
        # their email handle.
        # No placeholder ever reaches an enrollee: course, else cohort,
        # else the template's generic, course-free copy.
        "course_name": (course or "").strip() or (cohort or "").strip(),
        # Optional Studio setting: names the cohort's Slack channel in the
        # welcome copy. Blank is the shipping default and the template
        # branches so the sentence still reads cleanly.
        "course_channel": maven_course_slack_channel(),
        "password_reset_url": f"{site_url}/api/password-reset?token={reset_token}",
        "sign_in_url": f"{site_url}/accounts/login/",
        # The one thing we ask them to do after joining must be a real link.
        # Same destination and wording as community_invite.md.
        "onboarding_url": f"{site_url}/onboarding/",
        # /community/slack is @login_required + Main-gated. A signed-out
        # click redirects through login with next= preserved, so the copy no
        # longer needs a numbered set-password -> sign-in -> join sequence
        # (issue #1593).
        "slack_join_url": f"{site_url}/community/slack",
        "opt_out_url": f"{site_url}/api/maven-email-opt-out?token={opt_out_token}",
        # Issue #1593: the newsletter is opt-IN. This is the one click that
        # both verifies the address and subscribes them; nothing else in the
        # enrollment flow subscribes anybody. It is a distinct token action
        # from ``verify_email`` precisely so that ordinary verification can
        # never be mistaken for newsletter consent.
        "newsletter_opt_in_url": (
            f"{site_url}/api/verify-and-subscribe?token={opt_in_token}"
        ),
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
    for name in ("notification", "slack", "welcome"):
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
                return
            actions.append("Sent staff enrollment heads-up.")
        elif name == "slack":
            # ``skipped`` is "we correctly did nothing" — never ``succeeded``
            # (nothing happened) and never ``failed`` (nothing went wrong, and
            # a retry would only burn an attempt). ``failed`` is reserved for
            # a real, retryable Slack problem.
            slack_status, slack_note = _invite_to_slack(row.user, actions)
            if slack_status != MavenEnrollmentEvent.STEP_SUCCEEDED:
                _finish_step(pk, name, slack_status, slack_note)
                return
        elif name == "welcome":
            if not row.user.email_preferences.get("maven_emails", True):
                _finish_step(pk, name, MavenEnrollmentEvent.STEP_SKIPPED, "")
                actions.append("Maven welcome suppressed by scoped preference.")
                return
            _send_welcome(row.user, row.course, row.cohort, actions)
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
    if (
        occurrence.user.tier_id
        and occurrence.user.tier.level >= tier.level
        and occurrence.user.billing_period_end
        and occurrence.user.billing_period_end > now
    ):
        expiry_candidates.append(occurrence.user.billing_period_end)
    if grant is not None and grant.expires_at not in expiry_candidates:
        expiry_candidates.append(grant.expires_at)
    return tier, max(expiry_candidates)


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
