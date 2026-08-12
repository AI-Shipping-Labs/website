"""Studio report + read-only check for Stripe subscription reconciliation.

Issue #1308. Staff-only. The report is read-only and shows the latest run's
summary + findings. The ``Check all Stripe subscriptions`` action enqueues a
diagnostic (read-only) run and never applies changes. Bulk apply is
deliberately NOT offered in Studio — writes stay behind the confirmed API.
"""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from integrations.config import get_config
from jobs.tasks import async_task, build_task_name
from payments.models import MonthlyPaymentGrace as Grace
from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)
from payments.services import subscription_reconciliation as _recon
from payments.services.monthly_payment_grace import _effective_tier
from studio.decorators import staff_required
from studio.utils import studio_pagination_context

_FILTERS = {"actionable", "scheduled", "warnings", "payment_grace", "all"}
_TIER_FILTERS = {"basic", "main", "premium"}

_CLASSIFICATION_LABELS = {
    _recon.CLASSIFICATION_OK: "In sync",
    _recon.CLASSIFICATION_ACTIVE_METADATA: "Active metadata drift",
    _recon.CLASSIFICATION_SCHEDULED: "Scheduled cancellation",
    _recon.CLASSIFICATION_ENDED: "Ended subscription still entitled",
    _recon.CLASSIFICATION_SUSPECTED_MISSED_DELETE: (
        "Ended subscription still entitled"
    ),
    _recon.CLASSIFICATION_DUNNING: "Dunning / grace",
    _recon.CLASSIFICATION_MONTHLY_GRACE_ACTIVE: "Payment grace active",
    _recon.CLASSIFICATION_MONTHLY_GRACE_DUE: "Payment grace due",
    _recon.CLASSIFICATION_MONTHLY_GRACE_REVIEW: "Payment grace review",
    _recon.CLASSIFICATION_NON_ENTITLED_REVIEW: "Needs review",
    _recon.CLASSIFICATION_MISSING_SUBSCRIPTION: "Missing Stripe subscription",
    _recon.CLASSIFICATION_MISSING_LINK: "Missing Stripe link",
    _recon.CLASSIFICATION_INCONSISTENT_TAGS: "Inconsistent status tags",
    _recon.CLASSIFICATION_DUPLICATE_OWNERSHIP: "Duplicate Stripe ownership",
    _recon.CLASSIFICATION_AMBIGUOUS_SUBSCRIPTION: "Ambiguous subscription",
    _recon.CLASSIFICATION_UNKNOWN_PRICE: "Unknown price",
    _recon.CLASSIFICATION_LOOKUP_ERROR: "Lookup error",
}

_ACTION_LABELS = {
    _recon.ACTION_REVERT_TO_FREE: "Revert to Free",
    _recon.ACTION_REPAIR_ACTIVE_METADATA: "Repair metadata",
    _recon.ACTION_REPAIR_SCHEDULED_CANCELLATION: "Repair pending/date metadata",
    _recon.ACTION_REVIEW: "Review",
    _recon.ACTION_NOOP: "",
}

_EVIDENCE_LABELS = {
    Finding.WEBHOOK_PROCESSED: "Deletion webhook processed",
    Finding.WEBHOOK_FAILED_PERMANENT: "Deletion webhook failed",
    Finding.WEBHOOK_MISSING: "Deletion webhook missing",
    Finding.WEBHOOK_NOT_APPLICABLE: "",
}


def _stripe_url(account_id, resource, object_id):
    if not account_id or not object_id:
        return ""
    return f"https://dashboard.stripe.com/{account_id}/{resource}/{object_id}"


@staff_required
def subscription_reconciliation_report(request):
    active_filter = (request.GET.get("filter") or "all").lower()
    if active_filter not in _FILTERS:
        active_filter = "all"

    active_tier = (request.GET.get("tier") or "").lower()
    if active_tier not in _TIER_FILTERS:
        active_tier = ""

    latest_run = Run.objects.order_by("-started_at", "-id").first()

    findings_qs = Finding.objects.none()
    if latest_run is not None:
        findings_qs = latest_run.findings.all()
        if active_filter == "actionable":
            findings_qs = findings_qs.filter(
                classification__in=_recon.ACTIONABLE_CLASSIFICATIONS,
            )
        elif active_filter == "scheduled":
            findings_qs = findings_qs.filter(
                classification=_recon.CLASSIFICATION_SCHEDULED,
            )
        elif active_filter == "warnings":
            findings_qs = findings_qs.filter(outcome=Finding.OUTCOME_WARNING)
        if active_tier:
            findings_qs = findings_qs.filter(current_tier=active_tier)
        findings_qs = findings_qs.select_related("user").order_by(
            "classification", "email",
        )

    grace_qs = Grace.objects.none()
    if active_filter == "payment_grace":
        grace_qs = (
            Grace.objects.select_related("user__tier", "base_tier_at_start")
            .prefetch_related("deliveries")
        )
        if active_tier:
            grace_qs = grace_qs.filter(user__tier__slug=active_tier)
        grace_qs = grace_qs.order_by("-grace_started_at", "-created_at")
        pager = studio_pagination_context(request, grace_qs)
    else:
        pager = studio_pagination_context(request, findings_qs)
    stripe_account_id = get_config("STRIPE_DASHBOARD_ACCOUNT_ID", "")

    rows = []
    for f in ([] if active_filter == "payment_grace" else pager["page"].object_list):
        rows.append({
            "finding": f,
            "classification_label": _CLASSIFICATION_LABELS.get(
                f.classification, f.classification,
            ),
            "action_label": _ACTION_LABELS.get(f.action, f.action),
            "evidence_label": _EVIDENCE_LABELS.get(f.webhook_evidence, ""),
            "customer_url": _stripe_url(
                stripe_account_id, "customers", f.stripe_customer_id,
            ),
            "subscription_url": _stripe_url(
                stripe_account_id, "subscriptions", f.stripe_subscription_id,
            ),
        })

    grace_rows = []
    if active_filter == "payment_grace":
        for grace in pager["page"].object_list:
            effective = _effective_tier(grace.user)
            grace_rows.append({
                "grace": grace,
                "base_tier": grace.user.tier.slug if grace.user.tier_id else "free",
                "effective_tier": effective.slug if effective else "free",
                "customer_url": _stripe_url(
                    stripe_account_id, "customers", grace.stripe_customer_id,
                ),
                "subscription_url": _stripe_url(
                    stripe_account_id, "subscriptions", grace.stripe_subscription_id,
                ),
                "invoice_url": _stripe_url(
                    stripe_account_id, "invoices", grace.stripe_invoice_id,
                ),
            })

    evidence_counts = _webhook_evidence_counts(
        latest_run.findings.all() if latest_run else Finding.objects.none()
    )

    return render(
        request,
        "studio/payments/subscription_reconciliation.html",
        {
            "latest_run": latest_run,
            "rows": rows,
            "grace_rows": grace_rows,
            "payment_grace_count": Grace.objects.count(),
            "active_filter": active_filter,
            "active_tier": active_tier,
            "has_findings": findings_qs.exists() if latest_run else False,
            "evidence_processed": evidence_counts[Finding.WEBHOOK_PROCESSED],
            "evidence_failed": evidence_counts[Finding.WEBHOOK_FAILED_PERMANENT],
            "evidence_missing": evidence_counts[Finding.WEBHOOK_MISSING],
            **pager,
        },
    )


def _webhook_evidence_counts(findings_qs):
    counts = {
        Finding.WEBHOOK_PROCESSED: 0,
        Finding.WEBHOOK_FAILED_PERMANENT: 0,
        Finding.WEBHOOK_MISSING: 0,
    }
    for evidence in findings_qs.values_list("webhook_evidence", flat=True):
        if evidence in counts:
            counts[evidence] += 1
    return counts


@staff_required
@require_POST
def subscription_reconciliation_check(request):
    """Enqueue a read-only diagnostic run. Never applies changes."""
    run = Run.objects.create(
        status=Run.STATUS_QUEUED,
        mode=Run.MODE_DIAGNOSTIC,
        source=Run.SOURCE_STUDIO,
        requested_by=request.user,
    )
    async_task(
        "payments.tasks.subscription_reconciliation.run_queued_reconciliation",
        run_id=str(run.id),
        task_name=build_task_name(
            "Subscription reconciliation", "diagnostic", "studio",
        ),
    )
    messages.success(
        request,
        "Read-only check queued. It will not change member access.",
    )
    return redirect("studio_subscription_reconciliation")
