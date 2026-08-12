"""Scheduled monthly-payment grace jobs (issue #1413)."""

from payments.models import SubscriptionReconciliationRun as Run
from payments.services.monthly_payment_grace import (
    discover_from_reconciliation_run,
    sweep_payment_graces,
)


def run_scheduled_grace_discovery():
    run = (
        Run.objects.filter(
            source=Run.SOURCE_SCHEDULED,
            mode=Run.MODE_DIAGNOSTIC,
            status=Run.STATUS_COMPLETED,
        ).order_by("-finished_at", "-started_at").first()
    )
    return 0 if run is None else discover_from_reconciliation_run(run.pk)


def run_payment_grace_sweep():
    return sweep_payment_graces()
