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

_ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}

_RUN_EXAMPLE = {
    "id": "b0a1c2d3-4e5f-6071-8293-a4b5c6d7e8f9",
    "status": "completed",
    "mode": "diagnostic",
    "source": "scheduled",
    "started_at": "2026-07-25T04:30:00+00:00",
    "finished_at": "2026-07-25T04:31:12+00:00",
    "error_message": "",
    "counts": {
        "cohort": 42,
        "ok": 37,
        "scheduled_cancellation": 1,
        "actionable": 2,
        "warning": 2,
        "changed": 0,
    },
}

_FINDING_EXAMPLE = {
    "email": "someone@example.com",
    "user_id": 1234,
    "current_tier": "main",
    "current_subscription_id": "sub_old",
    "stripe_customer_id": "cus_xyz",
    "stripe_subscription_id": "sub_xyz",
    "stripe_status": "canceled",
    "cancel_at_period_end": False,
    "stripe_period_end": None,
    "stripe_tier": None,
    "classification": "ended_subscription_still_entitled",
    "action": "revert_to_free",
    "outcome": "would_change",
    "message": "Stripe subscription is canceled but the member is still Main locally.",
    "conflicting_user_ids": [],
    "webhook": {
        "event_id": None,
        "event_type": None,
        "event_status": None,
        "processed_at": None,
        "evidence": "missing",
    },
}

_RUNS_LIST_EXAMPLE = {
    "count": 1,
    "page": 1,
    "next_cursor": None,
    "runs": [_RUN_EXAMPLE],
}

_RUN_DETAIL_EXAMPLE = {
    "run": _RUN_EXAMPLE,
    "webhook_evidence_counts": {
        "processed": 1,
        "failed_permanent": 0,
        "missing": 1,
        "not_applicable": 0,
    },
    "count": 1,
    "page": 1,
    "next_cursor": None,
    "findings": [_FINDING_EXAMPLE],
}

_ENQUEUE_EXAMPLE = {
    "run_id": "b0a1c2d3-4e5f-6071-8293-a4b5c6d7e8f9",
    "status": "queued",
}

_PAGE_QUERY = {
    "page": {
        "type": "integer",
        "required": False,
        "description": "1-based page number (default 1).",
    },
    "page_size": {
        "type": "integer",
        "required": False,
        "description": (
            f"Rows per page (default {DEFAULT_PAGE_SIZE}, "
            f"max {MAX_PAGE_SIZE})."
        ),
    },
}


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
        "base_tier": f.current_tier,
        "effective_tier": f.effective_tier or f.current_tier,
        "latest_invoice": {
            "id": f.latest_invoice_id,
            "status": f.latest_invoice_status,
            "paid": f.latest_invoice_paid,
            "created_at": (
                f.latest_invoice_created.isoformat()
                if f.latest_invoice_created else None
            ),
            "collection_method": f.collection_method,
        },
        "interval": f.interval,
        "interval_count": f.interval_count,
        "payment_grace": ({
            "id": str(f.payment_grace_id),
            "status": f.payment_grace_status,
            "started_at": (
                f.payment_grace_started_at.isoformat()
                if f.payment_grace_started_at else None
            ),
            "expires_at": (
                f.payment_grace_expires_at.isoformat()
                if f.payment_grace_expires_at else None
            ),
        } if f.payment_grace_id else None),
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
    description=(
        "Persisted run history and diagnostic enqueue for the cohort-wide "
        "Stripe-vs-website subscription reconciliation (issue #1308). "
        "Staff-token-only; missing/non-staff tokens return 401 before any "
        "query or task enqueue. These endpoints are read-only — apply "
        "writes stay on ``POST /api/payments/tier-reconcile`` behind the "
        "explicit ``confirm=apply_stripe_truth`` contract."
    ),
    methods={
        "GET": {
            "summary": "Paginated run history.",
            "description": (
                "Returns completed, running, queued, and failed runs "
                "newest-first with their summary counts. ``next_cursor`` "
                "is the next page number when more rows remain, else "
                "``null``."
            ),
            "query": dict(_PAGE_QUERY),
            "responses": {
                200: {
                    "description": "Paginated run history.",
                    "example": _RUNS_LIST_EXAMPLE,
                },
                401: {
                    "description": "Missing or invalid staff token.",
                    "schema": _ERROR_REF,
                },
                422: {
                    "description": "Invalid ``page`` / ``page_size``.",
                    "schema": _ERROR_REF,
                },
            },
        },
        "POST": {
            "summary": "Enqueue a read-only cohort run (202).",
            "description": (
                "Enqueues a diagnostic (read-only) reconciliation run over "
                "the full Stripe cohort and returns ``202`` with the new "
                "run id and status. Accepts no apply mode and no body — a "
                "run never changes member access."
            ),
            "responses": {
                202: {
                    "description": "Run queued.",
                    "example": _ENQUEUE_EXAMPLE,
                },
                401: {
                    "description": (
                        "Missing or invalid staff token; no run is created."
                    ),
                    "schema": _ERROR_REF,
                },
            },
        },
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
    description=(
        "Read-only detail for a single reconciliation run plus its "
        "findings. Staff-token-only (401 before any query). Supports the "
        "same ``classification`` / ``tier`` / ``filter`` filters as the "
        "Studio report and returns ``next_cursor`` for pagination. Invalid "
        "filters return the project-standard 422 with the offending field "
        "in ``details.field``."
    ),
    methods={
        "GET": {
            "summary": "Run detail plus filtered findings.",
            "description": (
                "Findings are filterable by ``classification`` (any stable "
                "classification value), ``tier`` (the member's local base "
                "tier), and ``filter`` (a saved view). ``tier`` and "
                "``classification`` combine as AND. ``next_cursor`` is the "
                "next page number when more rows remain, else ``null``."
            ),
            "query": {
                "classification": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Filter findings to one stable classification "
                        "value (e.g. ``scheduled_cancellation``, "
                        "``ended_subscription_still_entitled``, "
                        "``dunning_grace``). Unknown values return 422 "
                        "with ``details.field = \"classification\"``."
                    ),
                },
                "tier": {
                    "type": "string",
                    "enum": ["basic", "main", "premium", "free"],
                    "required": False,
                    "description": (
                        "Filter findings to the member's local base tier. "
                        "Unknown values return 422 with "
                        "``details.field = \"tier\"``."
                    ),
                },
                "filter": {
                    "type": "string",
                    "enum": ["actionable", "scheduled", "warnings", "all"],
                    "required": False,
                    "description": (
                        "Saved view: ``actionable`` (entitlement drift that "
                        "can be applied), ``scheduled`` (scheduled "
                        "cancellations), ``warnings`` (review-only rows), or "
                        "``all`` (default)."
                    ),
                },
                **_PAGE_QUERY,
            },
            "responses": {
                200: {
                    "description": "Run detail plus filtered findings.",
                    "example": _RUN_DETAIL_EXAMPLE,
                },
                401: {
                    "description": "Missing or invalid staff token.",
                    "schema": _ERROR_REF,
                },
                404: {
                    "description": "Run not found.",
                    "schema": _ERROR_REF,
                },
                422: {
                    "description": (
                        "Invalid ``classification``, ``tier``, ``filter``, "
                        "or pagination value."
                    ),
                    "schema": _ERROR_REF,
                    "example": {
                        "error": "Unknown tier filter",
                        "code": "validation_error",
                        "details": {"field": "tier"},
                    },
                },
            },
        },
    },
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
        _recon.CLASSIFICATION_MONTHLY_GRACE_ACTIVE,
        _recon.CLASSIFICATION_MONTHLY_GRACE_DUE,
        _recon.CLASSIFICATION_MONTHLY_GRACE_REVIEW,
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
