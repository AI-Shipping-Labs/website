"""Run history / detail / enqueue API for subscription reconciliation (#1308).

Staff-token-only, read-only reporting plus a diagnostic-only enqueue endpoint.
Apply writes stay on ``POST /api/payments/tier-reconcile`` behind the explicit
confirmation contract — these run endpoints never apply changes.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from jobs.tasks import async_task, build_task_name
from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)

_VALID_TIERS = {"basic", "main", "premium", "free"}
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def _run_counts(run):
    return {
        "cohort": run.cohort_count,
        "ok": run.ok_count,
        "scheduled_cancellation": run.scheduled_cancellation_count,
        "actionable": run.actionable_count,
        "warning": run.warning_count,
        "changed": run.changed_count,
    }


def _serialize_run(run):
    return {
        "id": str(run.id),
        "status": run.status,
        "mode": run.mode,
        "source": run.source,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_message": run.error_message,
        "counts": _run_counts(run),
    }


def _serialize_finding(f):
    return {
        "email": f.email,
        "user_id": f.user_id,
        "current_tier": f.current_tier,
        "current_subscription_id": f.current_subscription_id,
        "stripe_customer_id": f.stripe_customer_id,
        "stripe_subscription_id": f.stripe_subscription_id,
        "stripe_status": f.stripe_status,
        "cancel_at_period_end": f.cancel_at_period_end,
        "stripe_period_end": (
            f.stripe_period_end.isoformat() if f.stripe_period_end else None
        ),
        "stripe_tier": f.stripe_tier,
        "classification": f.classification,
        "action": f.action,
        "outcome": f.outcome,
        "message": f.message,
        "conflicting_user_ids": f.conflicting_user_ids,
        "webhook": {
            "event_id": f.webhook_event_id,
            "event_type": f.webhook_event_type,
            "event_status": f.webhook_event_status,
            "processed_at": (
                f.webhook_processed_at.isoformat()
                if f.webhook_processed_at else None
            ),
            "evidence": f.webhook_evidence,
        },
    }


def _webhook_evidence_counts(findings_qs):
    counts = {
        Finding.WEBHOOK_PROCESSED: 0,
        Finding.WEBHOOK_FAILED_PERMANENT: 0,
        Finding.WEBHOOK_MISSING: 0,
        Finding.WEBHOOK_NOT_APPLICABLE: 0,
    }
    for evidence in findings_qs.values_list("webhook_evidence", flat=True):
        if evidence in counts:
            counts[evidence] += 1
    return counts


def _parse_page(request):
    try:
        page = int(request.GET.get("page", "1"))
        page_size = int(request.GET.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except (TypeError, ValueError):
        return None, None, error_response(
            "page and page_size must be integers",
            "validation_error",
            status=422,
        )
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        return None, None, error_response(
            f"page must be >= 1 and page_size in 1..{MAX_PAGE_SIZE}",
            "validation_error",
            status=422,
        )
    return page, page_size, None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Tier Reconciliation",
    summary="List or enqueue subscription reconciliation runs",
    methods={
        "GET": {"summary": "Paginated run history."},
        "POST": {"summary": "Enqueue a read-only cohort run (202)."},
    },
)
def reconciliation_runs_dispatch(request):
    """``/api/payments/tier-reconcile/runs`` — GET history, POST enqueue."""
    if request.method == "POST":
        return _enqueue_reconciliation_run(request)
    return _reconciliation_runs(request)


def _reconciliation_runs(request):
    """Paginated run history."""
    page, page_size, err = _parse_page(request)
    if err is not None:
        return err
    qs = Run.objects.all().order_by("-started_at", "-id")
    total = qs.count()
    start = (page - 1) * page_size
    runs = list(qs[start:start + page_size])
    next_cursor = str(page + 1) if start + page_size < total else None
    return JsonResponse({
        "count": total,
        "page": page,
        "next_cursor": next_cursor,
        "runs": [_serialize_run(r) for r in runs],
    })


@token_required
@require_methods("GET")
@openapi_spec(
    tag="Tier Reconciliation",
    summary="Get one subscription reconciliation run with findings",
    methods={"GET": {"summary": "Run detail plus filtered findings."}},
)
def reconciliation_run_detail(request, run_id):
    """``GET /api/payments/tier-reconcile/runs/<uuid>`` — detail + findings."""
    run = Run.objects.filter(pk=run_id).first()
    if run is None:
        return error_response("Run not found", "not_found", status=404)

    page, page_size, err = _parse_page(request)
    if err is not None:
        return err

    findings = run.findings.all()

    classification = (request.GET.get("classification") or "").strip()
    if classification:
        if classification not in _recon_classifications():
            return error_response(
                "Unknown classification filter",
                "validation_error",
                status=422,
                details={"field": "classification"},
            )
        findings = findings.filter(classification=classification)

    tier = (request.GET.get("tier") or "").strip().lower()
    if tier:
        if tier not in _VALID_TIERS:
            return error_response(
                "Unknown tier filter",
                "validation_error",
                status=422,
                details={"field": "tier"},
            )
        findings = findings.filter(current_tier=tier)

    view = (request.GET.get("filter") or "").strip().lower()
    if view:
        if view == "actionable":
            findings = findings.filter(
                classification__in=_actionable_classifications(),
            )
        elif view == "scheduled":
            findings = findings.filter(
                classification=_scheduled_classification(),
            )
        elif view == "warnings":
            findings = findings.filter(
                outcome=Finding.OUTCOME_WARNING,
            )
        elif view != "all":
            return error_response(
                "Unknown filter value",
                "validation_error",
                status=422,
                details={"field": "filter"},
            )

    findings = findings.order_by("classification", "email")
    total = findings.count()
    start = (page - 1) * page_size
    page_rows = list(findings[start:start + page_size])
    next_cursor = str(page + 1) if start + page_size < total else None

    return JsonResponse({
        "run": _serialize_run(run),
        "webhook_evidence_counts": _webhook_evidence_counts(run.findings.all()),
        "count": total,
        "page": page,
        "next_cursor": next_cursor,
        "findings": [_serialize_finding(f) for f in page_rows],
    })


def _enqueue_reconciliation_run(request):
    """Enqueue a diagnostic run. No apply mode — runs are always read-only."""
    run = Run.objects.create(
        status=Run.STATUS_QUEUED,
        mode=Run.MODE_DIAGNOSTIC,
        source=Run.SOURCE_API,
        requested_by=request.user if request.user.is_authenticated else None,
    )
    async_task(
        "payments.tasks.subscription_reconciliation.run_queued_reconciliation",
        run_id=str(run.id),
        task_name=build_task_name(
            "Subscription reconciliation", "diagnostic", "api",
        ),
    )
    return JsonResponse(
        {"run_id": str(run.id), "status": run.status},
        status=202,
    )


def _recon_classifications():
    from payments.services import subscription_reconciliation as _recon
    return {
        _recon.CLASSIFICATION_OK,
        _recon.CLASSIFICATION_ACTIVE_METADATA,
        _recon.CLASSIFICATION_SCHEDULED,
        _recon.CLASSIFICATION_ENDED,
        _recon.CLASSIFICATION_SUSPECTED_MISSED_DELETE,
        _recon.CLASSIFICATION_DUNNING,
        _recon.CLASSIFICATION_NON_ENTITLED_REVIEW,
        _recon.CLASSIFICATION_MISSING_SUBSCRIPTION,
        _recon.CLASSIFICATION_MISSING_LINK,
        _recon.CLASSIFICATION_INCONSISTENT_TAGS,
        _recon.CLASSIFICATION_DUPLICATE_OWNERSHIP,
        _recon.CLASSIFICATION_AMBIGUOUS_SUBSCRIPTION,
        _recon.CLASSIFICATION_UNKNOWN_PRICE,
        _recon.CLASSIFICATION_LOOKUP_ERROR,
    }


def _actionable_classifications():
    from payments.services import subscription_reconciliation as _recon
    return list(_recon.ACTIONABLE_CLASSIFICATIONS)


def _scheduled_classification():
    from payments.services import subscription_reconciliation as _recon
    return _recon.CLASSIFICATION_SCHEDULED
