"""Shared query and display policy for Maven occurrences needing attention."""

from django.db.models import Q
from django.utils import timezone

from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import (
    MAX_STEP_ATTEMPTS,
    RUNNING_STEP_LEASE,
    STEP_NAMES,
)

ATTENTION_AGE = RUNNING_STEP_LEASE


def failed_step_names(occurrence):
    """Return failed current steps in the canonical ledger order."""
    return [
        name
        for name in STEP_NAMES
        if getattr(occurrence, f"{name}_status")
        == MavenEnrollmentEvent.STEP_FAILED
    ]


def failed_occurrences(queryset=None):
    """Return occurrences whose current ledger contains any failed step."""
    queryset = queryset if queryset is not None else MavenEnrollmentEvent.objects.all()
    failed = Q()
    for name in STEP_NAMES:
        failed |= Q(**{f"{name}_status": MavenEnrollmentEvent.STEP_FAILED})
    return queryset.filter(failed)


def needs_attention_occurrences(queryset=None, *, now=None):
    """Return the distinct occurrence set on which automatic recovery stalled."""
    queryset = queryset if queryset is not None else MavenEnrollmentEvent.objects.all()
    cutoff = (now or timezone.now()) - ATTENTION_AGE
    attention = Q()
    for name in STEP_NAMES:
        status = f"{name}_status"
        attempts = f"{name}_attempts"
        attempted_at = f"{name}_attempted_at"
        completed_at = f"{name}_completed_at"
        exhausted = Q(
            **{
                status: MavenEnrollmentEvent.STEP_FAILED,
                f"{attempts}__gte": MAX_STEP_ATTEMPTS,
            }
        )
        stale_failed = Q(**{status: MavenEnrollmentEvent.STEP_FAILED}) & (
            Q(**{f"{completed_at}__lte": cutoff})
            | Q(
                **{
                    f"{completed_at}__isnull": True,
                    f"{attempted_at}__lte": cutoff,
                }
            )
        )
        stale_running = Q(
            **{
                status: MavenEnrollmentEvent.STEP_RUNNING,
                f"{attempted_at}__lte": cutoff,
            }
        )
        attention |= exhausted | stale_failed | stale_running
    return queryset.filter(attention).distinct()


def step_attention_reason(occurrence, name, *, now=None):
    """Return ``exhausted``/``stalled`` when one current step needs attention."""
    if name not in STEP_NAMES:
        raise ValueError("unknown Maven step")
    status = getattr(occurrence, f"{name}_status")
    attempts = getattr(occurrence, f"{name}_attempts")
    if status == MavenEnrollmentEvent.STEP_FAILED and attempts >= MAX_STEP_ATTEMPTS:
        return "exhausted"
    if status not in {
        MavenEnrollmentEvent.STEP_FAILED,
        MavenEnrollmentEvent.STEP_RUNNING,
    }:
        return ""
    changed_at = (
        getattr(occurrence, f"{name}_completed_at")
        if status == MavenEnrollmentEvent.STEP_FAILED
        else None
    ) or getattr(occurrence, f"{name}_attempted_at")
    cutoff = (now or timezone.now()) - ATTENTION_AGE
    if changed_at is not None and changed_at <= cutoff:
        return "stalled"
    return ""


def occurrence_attention_reasons(occurrence, *, now=None):
    """Return attention reasons keyed by step name for Studio/API consumers."""
    reasons = {}
    current_time = now or timezone.now()
    for name in STEP_NAMES:
        reason = step_attention_reason(occurrence, name, now=current_time)
        if reason:
            reasons[name] = reason
    return reasons
