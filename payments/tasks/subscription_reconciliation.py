"""Background tasks for Stripe subscription reconciliation (issue #1308).

- ``run_scheduled_reconciliation`` is the daily 04:30 UTC job. It ALWAYS runs
  in diagnostic/read-only mode, never applies changes, prevents a concurrent
  run, recovers a stale ``running`` run using a documented timeout, and alerts
  admins only for new/changed actionable findings (deduplicated) and after
  three consecutive failed runs.
- ``run_queued_reconciliation`` executes a run row an API/Studio caller has
  already created with ``status=queued``.
"""

from datetime import timedelta

from django.core.mail import mail_admins
from django.utils import timezone

from integrations.config import get_config
from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)
from payments.services import subscription_reconciliation as _recon

STUDIO_REPORT_PATH = "/studio/payments/subscription-reconciliation/"


def _stale_minutes():
    try:
        return max(5, int(get_config("STRIPE_RECONCILIATION_STALE_MINUTES", "120")))
    except (TypeError, ValueError):
        return 120


def _recover_stale_runs():
    """Mark long-stuck running runs failed so the next run can start."""
    cutoff = timezone.now() - timedelta(minutes=_stale_minutes())
    stale = Run.objects.filter(status=Run.STATUS_RUNNING, started_at__lt=cutoff)
    for run in stale:
        run.status = Run.STATUS_FAILED
        run.finished_at = timezone.now()
        run.error_message = "Marked failed: run exceeded the stale timeout."
        run.save(update_fields=["status", "finished_at", "error_message"])


def _has_active_run():
    return Run.objects.filter(
        status__in=[Run.STATUS_QUEUED, Run.STATUS_RUNNING],
    ).exists()


def run_scheduled_reconciliation():
    """Daily read-only cohort reconciliation. Returns the run id or None."""
    _recover_stale_runs()

    # A second concurrent scheduled reconciliation exits without starting.
    if _has_active_run():
        return None

    run = _recon.run_reconciliation(
        mode=Run.MODE_DIAGNOSTIC,
        source=Run.SOURCE_SCHEDULED,
        requested_by=None,
    )

    if run.status == Run.STATUS_FAILED:
        _maybe_send_failure_alert()
    else:
        _maybe_send_actionable_alert(run)

    return run.pk


def run_queued_reconciliation(run_id):
    """Execute a queued run created by an API/Studio caller."""
    run = Run.objects.filter(pk=run_id).first()
    if run is None or run.status != Run.STATUS_QUEUED:
        return None
    _recon.execute_run(run, users=None, confirm=False)
    return run.pk


def _previous_completed_diagnostic(run):
    return (
        Run.objects.filter(
            mode=Run.MODE_DIAGNOSTIC,
            status=Run.STATUS_COMPLETED,
        )
        .exclude(pk=run.pk)
        .filter(started_at__lt=run.started_at)
        .order_by("-started_at")
        .first()
    )


def _actionable_signature(run):
    """Map of email -> (classification, stripe_status) for actionable rows."""
    rows = Finding.objects.filter(
        run=run,
        classification__in=_recon.ACTIONABLE_CLASSIFICATIONS,
    ).values_list("email", "classification", "stripe_status")
    return {email: (cls, status) for email, cls, status in rows}


def _maybe_send_actionable_alert(run):
    """Alert admins only for new/changed actionable findings vs the prior run."""
    current = _actionable_signature(run)
    if not current:
        return
    previous_run = _previous_completed_diagnostic(run)
    previous = _actionable_signature(previous_run) if previous_run else {}

    new_or_changed = {
        email: sig for email, sig in current.items()
        if previous.get(email) != sig
    }
    if not new_or_changed:
        return

    mail_admins(
        subject="[AI Shipping Labs] Stripe subscription reconciliation: new actionable findings",
        message=(
            f"Run: {run.pk}\n"
            f"Cohort checked: {run.cohort_count}\n"
            f"Actionable: {run.actionable_count}\n"
            f"Scheduled cancellations: {run.scheduled_cancellation_count}\n"
            f"Warnings: {run.warning_count}\n"
            f"New/changed actionable findings: {len(new_or_changed)}\n"
            f"Report: {STUDIO_REPORT_PATH}"
        ),
        fail_silently=True,
    )


def _maybe_send_failure_alert():
    """Alert admins after three consecutive failed scheduled runs."""
    recent = list(
        Run.objects.filter(source=Run.SOURCE_SCHEDULED)
        .order_by("-started_at")[:3]
    )
    if len(recent) < 3 or any(r.status != Run.STATUS_FAILED for r in recent):
        return
    latest = recent[0]
    if latest.error_message.endswith("[alerted]"):
        return
    mail_admins(
        subject="[AI Shipping Labs] Scheduled Stripe reconciliation failed 3 times",
        message=(
            f"Latest run id: {latest.pk}\n"
            f"Latest error: {latest.error_message}\n"
            f"Report: {STUDIO_REPORT_PATH}"
        ),
        fail_silently=True,
    )
    latest.error_message = f"{latest.error_message} [alerted]"
    latest.save(update_fields=["error_message"])
