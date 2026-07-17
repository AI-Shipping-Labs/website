"""Shared querying for outbound email history.

Studio and both staff APIs use this module so canonical identity matching and
the exclusive delivery disposition cannot drift between surfaces.
"""

import datetime

from django.db.models import Case, CharField, Exists, OuterRef, Q, Value, When
from django.utils import timezone

from accounts.models import EmailAlias, User
from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from email_app.models import EmailLog, SesEvent

DISPOSITIONS = (
    "sent",
    "delivered",
    "opened",
    "clicked",
    "bounced",
    "complained",
)


def canonical_addresses(user):
    """Return normalized current-primary and alias addresses for ``user``."""
    return {
        normalized
        for normalized in (
            normalize_email(user.email),
            *user.email_aliases.values_list("email", flat=True),
        )
        if normalized
    }


def canonical_history_q(user):
    """Ownership/address union that preserves history through merges/renames."""
    query = Q(user_id=user.pk)
    for address in canonical_addresses(user):
        query |= Q(recipient_email__iexact=address)
    return query


def annotate_disposition(queryset):
    """Annotate each row with its one strongest observed disposition."""
    delivered = SesEvent.objects.filter(
        email_log_id=OuterRef("pk"),
        event_type=SesEvent.EVENT_TYPE_DELIVERY,
    )
    return queryset.annotate(
        has_delivery=Exists(delivered),
        disposition=Case(
            When(complained_at__isnull=False, then=Value("complained")),
            When(bounced_at__isnull=False, then=Value("bounced")),
            When(Q(clicked_at__isnull=False) | Q(clicks__gt=0), then=Value("clicked")),
            When(Q(opened_at__isnull=False) | Q(opens__gt=0), then=Value("opened")),
            When(has_delivery=True, then=Value("delivered")),
            default=Value("sent"),
            output_field=CharField(),
        ),
    )


def email_log_queryset():
    """Optimized base queryset used by all list/serialization callers."""
    return annotate_disposition(
        EmailLog.objects.select_related("user", "campaign")
    )


def user_history_queryset(user):
    return (
        email_log_queryset()
        .filter(canonical_history_q(user))
        .distinct()
        .order_by("-sent_at", "-pk")
    )


def apply_recipient_search(queryset, search):
    """Apply snapshot substring search plus exact canonical expansion."""
    search = (search or "").strip()
    if not search:
        return queryset

    query = Q(recipient_email__icontains=search) | Q(
        recipient_email="",
        user__email__icontains=search,
    )
    canonical_user = resolve_user_by_email(search)
    if canonical_user is not None:
        query |= canonical_history_q(canonical_user)
    return queryset.filter(query).distinct()


def utc_date_bounds(since=None, until=None):
    """Convert inclusive UTC dates into half-open aware datetime bounds."""
    lower = None
    upper = None
    if since is not None:
        lower = timezone.make_aware(
            datetime.datetime.combine(since, datetime.time.min),
            datetime.UTC,
        )
    if until is not None:
        upper = timezone.make_aware(
            datetime.datetime.combine(until + datetime.timedelta(days=1), datetime.time.min),
            datetime.UTC,
        )
    return lower, upper


def apply_email_log_filters(
    queryset, *, search="", kind="", status="", since=None, until=None,
):
    queryset = apply_recipient_search(queryset, search)
    if kind:
        queryset = queryset.filter(email_type=kind)
    if status:
        queryset = queryset.filter(disposition=status)
    lower, upper = utc_date_bounds(since, until)
    if lower is not None:
        queryset = queryset.filter(sent_at__gte=lower)
    if upper is not None:
        queryset = queryset.filter(sent_at__lt=upper)
    return queryset


def recipient_user_map(logs):
    """Resolve address-only row recipients in two bulk queries, not per row."""
    addresses = {
        normalize_email(log.recipient_email)
        for log in logs
        if log.user_id is None and log.recipient_email
    }
    addresses.discard("")
    if not addresses:
        return {}

    primary_query = Q()
    for address in addresses:
        primary_query |= Q(email__iexact=address)
    mapping = {
        normalize_email(user.email): user
        for user in User.objects.filter(primary_query, is_active=True)
    }
    unresolved = addresses - set(mapping)
    if unresolved:
        for alias in EmailAlias.objects.select_related("user").filter(email__in=unresolved):
            mapping.setdefault(alias.email, alias.user)
    return mapping


def displayed_recipient(log):
    """Snapshot first; legacy related-user fallback second."""
    return log.recipient_email or (log.user.email if log.user_id else "")
