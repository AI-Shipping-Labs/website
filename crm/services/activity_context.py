"""Shared member activity context for CRM, Studio, and API surfaces."""

from urllib.parse import urlsplit

from django.db.models import Count

from analytics.activity import resolve_activity_target_urls
from analytics.models import UserActivity

ACTIVITY_CATEGORY_ALL = 'all'
ACTIVITY_CATEGORY_LEARNING = 'learning'
ACTIVITY_CATEGORY_EVENTS = 'events'
ACTIVITY_CATEGORY_CONTENT = 'content'
ACTIVITY_CATEGORY_ACCOUNT = 'account'
ACTIVITY_CATEGORY_COMMS = 'comms'

ACTIVITY_CATEGORIES = (
    ACTIVITY_CATEGORY_LEARNING,
    ACTIVITY_CATEGORY_EVENTS,
    ACTIVITY_CATEGORY_CONTENT,
    ACTIVITY_CATEGORY_ACCOUNT,
    ACTIVITY_CATEGORY_COMMS,
)

ACTIVITY_CATEGORY_LABELS = {
    ACTIVITY_CATEGORY_ALL: 'All',
    ACTIVITY_CATEGORY_LEARNING: 'Learning',
    ACTIVITY_CATEGORY_EVENTS: 'Events',
    ACTIVITY_CATEGORY_CONTENT: 'Content',
    ACTIVITY_CATEGORY_ACCOUNT: 'Account',
    ACTIVITY_CATEGORY_COMMS: 'Comms',
}

ACTIVITY_EVENT_CATEGORIES = {
    UserActivity.EVENT_COURSE_ENROLL: ACTIVITY_CATEGORY_LEARNING,
    UserActivity.EVENT_LESSON_OPEN: ACTIVITY_CATEGORY_LEARNING,
    UserActivity.EVENT_EVENT_REGISTER: ACTIVITY_CATEGORY_EVENTS,
    UserActivity.EVENT_EVENT_JOIN: ACTIVITY_CATEGORY_EVENTS,
    UserActivity.EVENT_RESOURCE_VIEW: ACTIVITY_CATEGORY_CONTENT,
    UserActivity.EVENT_SIGNUP: ACTIVITY_CATEGORY_ACCOUNT,
    UserActivity.EVENT_PAYMENT: ACTIVITY_CATEGORY_ACCOUNT,
    UserActivity.EVENT_EMAIL_CLICK: ACTIVITY_CATEGORY_COMMS,
    UserActivity.EVENT_SLACK_JOIN: ACTIVITY_CATEGORY_COMMS,
}

RELEVANT_ACTIVITY_TYPES = tuple(ACTIVITY_EVENT_CATEGORIES)

DEFAULT_ACTIVITY_LIMIT = 30
MAX_ACTIVITY_LIMIT = 100
PROFILE_ACTIVITY_LIMIT = 10


def normalize_activity_category(value):
    """Return a known activity category slug, defaulting invalid input to all."""
    raw = (value or ACTIVITY_CATEGORY_ALL).strip().lower()
    if raw == ACTIVITY_CATEGORY_ALL or raw in ACTIVITY_CATEGORIES:
        return raw
    return ACTIVITY_CATEGORY_ALL


def is_valid_activity_category(value):
    raw = (value or ACTIVITY_CATEGORY_ALL).strip().lower()
    return raw == ACTIVITY_CATEGORY_ALL or raw in ACTIVITY_CATEGORIES


def clamp_activity_limit(value, *, default=DEFAULT_ACTIVITY_LIMIT):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    if limit < 1:
        limit = default
    return min(limit, MAX_ACTIVITY_LIMIT)


def _event_types_for_category(category):
    if category == ACTIVITY_CATEGORY_ALL:
        return RELEVANT_ACTIVITY_TYPES
    return tuple(
        event_type
        for event_type, event_category in ACTIVITY_EVENT_CATEGORIES.items()
        if event_category == category
    )


def _strip_query_and_fragment(target_url):
    """Expose same-site activity paths without query strings or fragments."""
    if not target_url:
        return ''
    parsed = urlsplit(target_url)
    if not parsed.path:
        return ''
    return parsed.path


def _category_counts(qs):
    counts = {category: 0 for category in ACTIVITY_CATEGORIES}
    for row in qs.values('event_type').annotate(count=Count('id')):
        category = ACTIVITY_EVENT_CATEGORIES.get(row['event_type'])
        if category:
            counts[category] += row['count']
    counts[ACTIVITY_CATEGORY_ALL] = sum(
        counts[category] for category in ACTIVITY_CATEGORIES
    )
    return counts


def _serialize_activity(activity, target_url):
    category = ACTIVITY_EVENT_CATEGORIES[activity.event_type]
    return {
        'id': activity.pk,
        'event_type': activity.event_type,
        'type_label': activity.get_event_type_display(),
        'category': category,
        'category_label': ACTIVITY_CATEGORY_LABELS[category],
        'label': activity.label or activity.get_event_type_display(),
        'occurred_at': activity.occurred_at,
        'object_type': activity.object_type or '',
        'object_id': activity.object_id or '',
        'target_url': _strip_query_and_fragment(target_url),
        'is_payment': activity.event_type == UserActivity.EVENT_PAYMENT,
        'is_upgrade_marker': False,
    }


def build_activity_context(
    user,
    *,
    limit=DEFAULT_ACTIVITY_LIMIT,
    category=ACTIVITY_CATEGORY_ALL,
    since=None,
    include_category_counts=False,
):
    """Return serialized recent activity for a user.

    The shape is shared by the CRM profile, Studio user profile, prompt
    context, and staff-token API so link policy and upgrade-marker semantics
    cannot drift between operator surfaces.
    """
    category = normalize_activity_category(category)
    limit = clamp_activity_limit(limit)

    base_qs = UserActivity.objects.filter(
        user=user,
        event_type__in=RELEVANT_ACTIVITY_TYPES,
    )
    if since is not None:
        base_qs = base_qs.filter(occurred_at__gte=since)

    category_counts = None
    if include_category_counts:
        category_counts = _category_counts(base_qs)

    qs = base_qs
    event_types = _event_types_for_category(category)
    if event_types != RELEVANT_ACTIVITY_TYPES:
        qs = qs.filter(event_type__in=event_types)

    rows = list(qs.order_by('-occurred_at')[:limit])
    target_urls = resolve_activity_target_urls(rows)
    activities = [
        _serialize_activity(activity, target_urls.get(activity.pk, ''))
        for activity in rows
    ]

    if include_category_counts and category_counts is not None:
        total = category_counts[category]
    elif len(rows) < limit:
        total = len(rows)
    else:
        total = qs.count()

    first_payment_at = None
    for activity in activities:
        if activity['is_payment']:
            first_payment_at = activity['occurred_at']
    for activity in activities:
        activity['is_upgrade_marker'] = (
            first_payment_at is not None
            and activity['is_payment']
            and activity['occurred_at'] == first_payment_at
        )

    if category_counts is None:
        category_counts = {}

    return {
        'activities': activities,
        'activity_total': total,
        'activity_limit': limit,
        'activity_has_more': total > limit,
        'first_payment_at': first_payment_at,
        'active_activity_category': category,
        'activity_category_counts': category_counts,
    }


def serialize_activity_for_api(activity):
    """Return the JSON-ready row shape required by the staff activity API."""
    occurred_at = activity['occurred_at']
    return {
        'id': activity['id'],
        'event_type': activity['event_type'],
        'type_label': activity['type_label'],
        'category': activity['category'],
        'label': activity['label'],
        'occurred_at': occurred_at.isoformat() if occurred_at else None,
        'object_type': activity['object_type'],
        'object_id': activity['object_id'],
        'target_url': activity['target_url'],
        'is_upgrade_marker': activity['is_upgrade_marker'],
    }
