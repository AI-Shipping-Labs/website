"""Studio views for managing temporary tier overrides."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from content.access import annotate_override_raises_access
from payments.models import Tier, TierOverride
from studio.decorators import staff_required
from studio.views.user_search import studio_user_search as studio_user_search

User = get_user_model()

# Duration choices: label -> timedelta/relativedelta
DURATION_CHOICES = [
    ('14 days', timedelta(days=14)),
    ('1 month', relativedelta(months=1)),
    ('3 months', relativedelta(months=3)),
    ('6 months', relativedelta(months=6)),
    ('12 months', relativedelta(months=12)),
]


# Issue #1936: ``?status=`` values on the Active overrides list. Anything
# else (missing, blank, unknown) means All. The revoke redirect checks the
# posted value against this same tuple before putting it into a URL.
STATUS_EFFECTIVE = 'effective'
STATUS_INEFFECTIVE = 'ineffective'
OVERRIDE_STATUS_FILTERS = (STATUS_EFFECTIVE, STATUS_INEFFECTIVE)


def normalize_override_status_filter(value):
    """Return ``value`` when it is a known status filter, else ``''`` (All)."""
    return value if value in OVERRIDE_STATUS_FILTERS else ''


def _active_overrides_queryset():
    return annotate_override_raises_access(
        TierOverride.objects
        .filter(is_active=True, expires_at__gt=timezone.now())
        .select_related(
            'user__membership__tier', 'override_tier', 'original_tier',
            'granted_by',
        )
        .order_by('expires_at')
    )


def _filter_by_status(queryset, status):
    if status == STATUS_EFFECTIVE:
        return queryset.filter(raises_access=True)
    if status == STATUS_INEFFECTIVE:
        return queryset.filter(raises_access=False)
    return queryset


def _stored_tier_for(user):
    """Return ``(name, slug)`` of the member's current stored tier.

    Reads the ``select_related`` membership, so no query per row. A missing
    membership row or an unset tier is Free, matching
    ``resolve_effective_tier``.
    """
    membership = getattr(user, 'membership', None)
    tier = membership.tier if membership is not None else None
    if tier is None:
        return 'Free', 'free'
    return tier.name, tier.slug


def _user_override_context(user):
    active_override = (
        TierOverride.objects
        .filter(user=user, is_active=True, expires_at__gt=timezone.now())
        .select_related('override_tier', 'granted_by')
        .first()
    )
    override_history = (
        TierOverride.objects
        .filter(user=user)
        .select_related('override_tier', 'original_tier', 'granted_by')
        .order_by('-created_at')
    )
    # Override authoring is intentionally based on the stored base tier, not
    # effective access, so staff can grant only tiers above the real subscription.
    # Issue #1579: the base tier lives on payments.Membership.
    current_level = user.membership.tier.level if user.membership.tier_id else 0
    highest_tier = Tier.objects.order_by('-level').first()
    is_highest_tier = bool(highest_tier and current_level >= highest_tier.level)
    available_tiers = []
    if not is_highest_tier:
        available_tiers = list(
            Tier.objects.filter(level__gt=current_level).order_by('level')
        )
    return {
        'detail_user': user,
        'active_override': active_override,
        'override_history': override_history,
        'available_tiers': available_tiers,
        'is_highest_tier': is_highest_tier,
        'duration_labels': [label for label, _ in DURATION_CHOICES],
    }


@staff_required
def tier_override_page(request):
    """Tier override entry point with autocomplete and active override list.

    Issue #1936: ``?status=effective|ineffective`` narrows the list to grants
    that raise (or do not raise) the member's access. The chip counts are
    always the unfiltered totals. Query count is constant in the number of
    rows: one aggregate for the counts and one select for the rows.
    """
    status_filter = normalize_override_status_filter(
        request.GET.get('status', ''),
    )
    base = _active_overrides_queryset()
    counts = base.aggregate(
        total=Count('pk'),
        effective=Count('pk', filter=Q(raises_access=True)),
    )
    total = counts['total']
    effective = counts['effective']
    overrides = list(_filter_by_status(base, status_filter))
    for override in overrides:
        stored_name, stored_slug = _stored_tier_for(override.user)
        override.stored_tier_name = stored_name
        override.stored_tier_slug = stored_slug
        original = override.original_tier
        override.tier_at_grant_name = (
            original.name
            if original is not None and original.slug != stored_slug
            else ''
        )
    list_url = reverse('studio_tier_overrides_list')
    status_chips = [
        {
            'key': key or 'all',
            'label': label,
            'count': count,
            'url': f'{list_url}?status={key}' if key else list_url,
            'selected': status_filter == key,
        }
        for key, label, count in (
            ('', 'All', total),
            (STATUS_EFFECTIVE, 'Raising access', effective),
            (STATUS_INEFFECTIVE, 'Not raising access', total - effective),
        )
    ]
    return render(request, 'studio/tier_overrides.html', {
        'active_overrides': overrides,
        'status_filter': status_filter,
        'status_chips': status_chips,
        'override_counts': {
            'total': total,
            'effective': effective,
            'ineffective': total - effective,
        },
    })


@staff_required
def user_tier_override_page(request, user_id):
    """Per-user tier override management page."""
    user = get_object_or_404(
        User.objects.select_related('membership__tier'), pk=user_id,
    )
    return render(
        request,
        'studio/users/tier_override.html',
        _user_override_context(user),
    )


