"""Read-only, privacy-safe classification of terminal durable job intents."""

from community_base.jobs.models import JobIntent
from community_base.jobs.registry import registered_handler_names
from django.db.models import Case, CharField, Count, F, Max, Min, Value, When

MAX_REPORTED_HANDLERS = 100
SAFE_ERROR_CATEGORIES = (
    "attempts_exhausted",
    "discarded_by_operator",
    "handler_error",
    "lease_expired",
    "permanent_error",
    "relay_not_configured",
    "retryable_error",
    "unknown_handler",
)


def _handler_category():
    names = sorted(registered_handler_names())
    return Case(
        When(handler__in=names[:MAX_REPORTED_HANDLERS], then=F("handler")),
        When(handler__in=names[MAX_REPORTED_HANDLERS:],
             then=Value("other_registered_handler")),
        default=Value("unknown_handler"),
        output_field=CharField(),
    )


def dead_intent_summary():
    """Collapse untrusted labels in SQL before grouping; never truncate counts."""
    groups = list(
        JobIntent.objects.filter(status=JobIntent.Status.DEAD).order_by()
        .annotate(handler_category=_handler_category(), error_category=Case(
            When(last_error__in=SAFE_ERROR_CATEGORIES, then=F("last_error")),
            default=Value("unknown_error"), output_field=CharField(),
        ))
        .values("handler_category", "error_category")
        .annotate(count=Count("pk"), oldest_created_at=Min("created_at"),
                  newest_created_at=Max("created_at"))
        .order_by("handler_category", "error_category")
    )
    # One aggregate snapshot keeps dates and totals consistent during new failures.
    return {
        "total_dead": sum(group["count"] for group in groups),
        "oldest_created_at": min(
            (group["oldest_created_at"] for group in groups), default=None,
        ),
        "newest_created_at": max(
            (group["newest_created_at"] for group in groups), default=None,
        ),
        "disposition": "retained_dead",
        "retry_policy": "no_automatic_retry",
        "groups": groups,
    }
