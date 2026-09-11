"""Declarative, atomic reconciliation for recurring django-q schedules."""

import inspect
import logging
import pydoc
import re
from dataclasses import dataclass, field
from typing import Any

from django.core.cache import caches
from django.db import transaction
from django.utils import timezone
from django_q.models import Schedule

from jobs.tasks.helpers import schedule

logger = logging.getLogger(__name__)

SCHEDULE_RECONCILIATION_CACHE_ALIAS = "django_q"
SCHEDULE_RECONCILIATION_CACHE_KEY = "schedule_reconciliation"


@dataclass(frozen=True)
class ScheduleDefinition:
    name: str
    func: str
    cron: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    preserve_disabled: bool = False
    r2_only: bool = False
    description: str = ""


SCHEDULE_DEFINITIONS = (
    ScheduleDefinition(
        "health-check", "jobs.tasks.healthcheck.health_check", "*/15 * * * *", description="every 15 min"
    ),
    ScheduleDefinition(
        "campaign-delivery-recovery",
        "email_app.tasks.campaign_delivery_recovery.recover_campaign_deliveries",
        "*/5 * * * *",
        description="every 5 min",
    ),
    ScheduleDefinition(
        "clear-expired-sessions",
        "jobs.tasks.cleanup.clear_expired_sessions",
        "50 2 * * *",
        description="daily at 02:50 UTC",
    ),
    ScheduleDefinition(
        "cleanup-webhook-logs",
        "jobs.tasks.cleanup.cleanup_old_webhook_logs",
        "0 3 * * *",
        {"days": 30},
        description="daily at 3 AM",
    ),
    ScheduleDefinition(
        "cleanup-calendly-webhook-logs",
        "jobs.tasks.cleanup.cleanup_calendly_webhook_logs",
        "5 3 * * *",
        r2_only=True,
        description="daily at 03:05 UTC",
    ),
    ScheduleDefinition(
        "retry-calendly-webhooks",
        "jobs.tasks.calendly.retry_calendly_webhooks",
        "*/5 * * * *",
        r2_only=True,
        description="every 5 min",
    ),
    ScheduleDefinition(
        "cleanup-webhook-deliveries",
        "jobs.tasks.cleanup.cleanup_old_webhook_deliveries",
        "10 3 * * *",
        {"days": 30},
        description="daily at 03:10 UTC",
    ),
    ScheduleDefinition(
        "resume-webhook-deliveries",
        "triggers.tasks.resume_due_webhook_deliveries",
        "* * * * *",
        r2_only=True,
        description="every minute",
    ),
    ScheduleDefinition(
        "redact-maven-enrollment-pii",
        "jobs.tasks.cleanup.redact_old_maven_enrollment_pii",
        "20 3 * * *",
        {"days": 30},
        r2_only=True,
        description="daily at 03:20 UTC",
    ),
    ScheduleDefinition(
        "retry-maven-enrollment-steps",
        "jobs.tasks.cleanup.retry_maven_enrollment_steps",
        "*/5 * * * *",
        r2_only=True,
        description="five-minute cadence",
    ),
    ScheduleDefinition(
        "retry-stuck-recording-uploads",
        "jobs.tasks.recording_upload.retry_stuck_recording_uploads",
        "*/5 * * * *",
        r2_only=True,
        description="every 5 min",
    ),
    ScheduleDefinition(
        "purge-user-activity", "analytics.tasks.purge_old_user_activity", "30 3 * * *", description="daily at 03:30 UTC"
    ),
    ScheduleDefinition(
        "purge-plan-sprints-raw-text",
        "crm.tasks.purge_plan_sprints_raw_text.purge_plan_sprints_raw_text",
        "40 3 * * *",
        r2_only=True,
        description="daily at 03:40 UTC",
    ),
    ScheduleDefinition(
        "event-reminders",
        "notifications.services.event_reminders.check_event_reminders",
        "*/15 * * * *",
        description="every 15 min",
    ),
    ScheduleDefinition(
        "complete-finished-events",
        "events.tasks.complete_finished_events.complete_finished_events",
        "0 4 * * *",
        description="daily at 04:00 UTC",
    ),
    ScheduleDefinition(
        "expire-tier-overrides",
        "jobs.tasks.expire_overrides.expire_tier_overrides",
        "*/15 * * * *",
        description="every 15 min",
    ),
    ScheduleDefinition(
        "slack-membership-refresh",
        "community.tasks.slack_membership.refresh_slack_membership",
        "0 6 * * *",
        description="daily membership and channel reconciliation at 06:00 UTC",
    ),
    ScheduleDefinition(
        "import-slack-daily",
        "accounts.tasks.run_scheduled_import",
        "0 3 * * *",
        {"source": "slack"},
        preserve_disabled=True,
        description="daily at 03:00 UTC",
    ),
    ScheduleDefinition(
        "import-stripe-daily",
        "accounts.tasks.run_scheduled_import",
        "30 3 * * *",
        {"source": "stripe"},
        preserve_disabled=True,
        description="daily at 03:30 UTC",
    ),
    ScheduleDefinition(
        "stripe-subscription-reconciliation-daily",
        "payments.tasks.subscription_reconciliation.run_scheduled_reconciliation",
        "30 4 * * *",
        description="daily at 04:30 UTC",
    ),
    ScheduleDefinition(
        "stripe-monthly-payment-grace-discovery-daily",
        "payments.tasks.monthly_payment_grace.run_scheduled_grace_discovery",
        "45 4 * * *",
        description="daily at 04:45 UTC",
    ),
    ScheduleDefinition(
        "stripe-monthly-payment-grace-sweep",
        "payments.tasks.monthly_payment_grace.run_payment_grace_sweep",
        "*/15 * * * *",
        description="every 15 minutes",
    ),
    ScheduleDefinition(
        "remind-unverified-users",
        "accounts.tasks.remind_unverified_users.remind_unverified_users",
        "0 7 * * *",
        description="daily at 07:00 UTC",
    ),
    ScheduleDefinition(
        "purge-unverified-users",
        "accounts.tasks.purge_unverified_users.purge_unverified_users",
        "0 8 * * *",
        description="daily at 08:00 UTC",
    ),
    ScheduleDefinition(
        "ingest-plan-sprints",
        "crm.tasks.ingest_plan_sprints.ingest_plan_sprints",
        "0 5 * * *",
        description="daily at 05:00 UTC",
    ),
    ScheduleDefinition(
        "sprint-cadence-notifications",
        "plans.tasks.sprint_cadence.send_sprint_cadence_notifications",
        "15 5 * * *",
        description="daily at 05:15 UTC",
    ),
    ScheduleDefinition(
        "sprint-end-recaps",
        "plans.tasks.sprint_end.send_sprint_end_recaps",
        "30 5 * * *",
        description="daily at 05:30 UTC",
    ),
    ScheduleDefinition(
        "onboarding-reminders",
        "accounts.tasks.remind_onboarding.remind_onboarding_incomplete",
        "30 6 * * *",
        description="daily at 06:30 UTC",
    ),
    ScheduleDefinition(
        "onboarding-staff-notification-recovery",
        "questionnaires.tasks.reconcile_onboarding_staff_notifications",
        "*/5 * * * *",
        r2_only=True,
        description="five-minute cadence",
    ),
    ScheduleDefinition(
        "cb-jobs-run-due", "jobs.tasks.community_base_jobs.run_due_jobs", "* * * * *", description="every minute"
    ),
    ScheduleDefinition(
        "cb-jobs-sweep", "jobs.tasks.community_base_jobs.sweep_jobs", "*/5 * * * *", description="five-minute cadence"
    ),
    ScheduleDefinition(
        "reconcile-schedules",
        "jobs.tasks.schedule_reconciliation.reconcile_schedules",
        "*/15 * * * *",
        description="every 15 min",
    ),
)

R2_ONLY_SCHEDULE_NAMES = tuple(definition.name for definition in SCHEDULE_DEFINITIONS if definition.r2_only)


def build_schedule_definitions(*, background_enabled):
    """Build the complete schedule set for the current release mode."""
    return tuple(definition for definition in SCHEDULE_DEFINITIONS if background_enabled or not definition.r2_only)


def validate_schedule_definitions(definitions):
    """Validate every declaration before the first Schedule write."""
    names = [definition.name for definition in definitions]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate schedule names: {', '.join(duplicates)}")

    for definition in definitions:
        if not definition.name:
            raise ValueError("schedule name is required")
        if not definition.cron:
            raise ValueError(f"cron is required for schedule {definition.name!r}")
        target = pydoc.locate(definition.func)
        if not inspect.isfunction(target):
            raise ValueError(f"schedule {definition.name!r} func={definition.func!r} must resolve to a function")
    return tuple(definitions)


def expected_schedule_names(definitions):
    return sorted(definition.name for definition in definitions)


def _short_error(exc):
    text = " ".join(str(exc).split())
    text = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(password|secret|token|authorization|api[_-]?key)\b\s*([=:])\s*\S+",
        r"\1\2[redacted]",
        text,
    )
    return f"{type(exc).__name__}: {text}"[:500]


def publish_schedule_reconciliation_state(*, status, expected_names, error=None):
    """Persist the latest reconciliation result without masking its outcome."""
    try:
        cache = caches[SCHEDULE_RECONCILIATION_CACHE_ALIAS]
        previous = cache.get(SCHEDULE_RECONCILIATION_CACHE_KEY) or {}
        now = timezone.now().isoformat()
        expected_names = sorted(expected_names)
        present_names = set(
            Schedule.objects.filter(name__in=expected_names).values_list(
                "name",
                flat=True,
            )
        )
        payload = {
            "status": status,
            "recorded_at": now,
            "last_success_at": (now if status == "ok" else previous.get("last_success_at")),
            "error": None if error is None else _short_error(error),
            "expected_names": expected_names,
            "missing_names": sorted(set(expected_names) - present_names),
        }
        cache.set(SCHEDULE_RECONCILIATION_CACHE_KEY, payload, timeout=None)
        return payload
    except Exception:
        logger.exception("publish schedule reconciliation state failed")
        return None


def apply_schedule_definitions(definitions):
    """Validate and atomically apply a complete declarative schedule set."""
    definitions = tuple(definitions)
    expected_names = expected_schedule_names(definitions)
    try:
        definitions = validate_schedule_definitions(definitions)
        with transaction.atomic():
            for definition in definitions:
                schedule(
                    definition.func,
                    cron=definition.cron,
                    name=definition.name,
                    preserve_disabled=definition.preserve_disabled,
                    **definition.kwargs,
                )
    except Exception as exc:
        publish_schedule_reconciliation_state(
            status="degraded",
            expected_names=expected_names,
            error=exc,
        )
        raise

    publish_schedule_reconciliation_state(
        status="ok",
        expected_names=expected_names,
    )
    return definitions
