"""Studio views for managing temporary tier overrides."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import (
    Http404,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from content.access import LEVEL_MAIN
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment
from studio.decorators import staff_required

User = get_user_model()

# Duration choices: label -> timedelta/relativedelta
DURATION_CHOICES = [
    ('14 days', timedelta(days=14)),
    ('1 month', relativedelta(months=1)),
    ('3 months', relativedelta(months=3)),
    ('6 months', relativedelta(months=6)),
    ('12 months', relativedelta(months=12)),
]


class HttpResponsePermanentPreserveMethodRedirect(HttpResponseRedirect):
    status_code = 308


def _preserve_method_redirect(url):
    return HttpResponsePermanentPreserveMethodRedirect(url)


def _legacy_redirect(request, url):
    if request.method == 'POST':
        return _preserve_method_redirect(url)
    return HttpResponsePermanentRedirect(url)


def _active_overrides_queryset():
    return (
        TierOverride.objects
        .filter(is_active=True, expires_at__gt=timezone.now())
        .select_related('user', 'override_tier', 'original_tier', 'granted_by')
        .order_by('expires_at')
    )


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
    current_level = user.tier.level if user.tier_id else 0
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
    """Tier override entry point with autocomplete and active override list."""
    return render(request, 'studio/tier_overrides.html', {
        'active_overrides': _active_overrides_queryset(),
    })


@staff_required
def user_tier_override_page(request, user_id):
    """Per-user tier override management page."""
    user = get_object_or_404(User.objects.select_related('tier'), pk=user_id)
    return render(
        request,
        'studio/users/tier_override.html',
        _user_override_context(user),
    )


def _display_name_for(user):
    """Return the picker display name for a user.

    Prefers ``"First Last"`` (trimmed) when at least one of ``first_name`` or
    ``last_name`` is non-empty; otherwise falls back to the email so every
    suggestion row still has something to render.
    """
    full = (user.get_full_name() or '').strip()
    return full or user.email


def _relevance_rank(user, query_lower):
    """Sort key: lower is better.

    Band 0: exact match on email or full name (case-insensitive).
    Band 1: startswith match on email, first_name, last_name, or full name.
    Band 2: substring match (any other case-insensitive containment).
    """
    email_l = user.email.lower()
    first_l = (user.first_name or '').lower()
    last_l = (user.last_name or '').lower()
    full_l = f'{first_l} {last_l}'.strip()
    if email_l == query_lower or full_l == query_lower:
        return 0
    if (
        email_l.startswith(query_lower)
        or first_l.startswith(query_lower)
        or last_l.startswith(query_lower)
        or full_l.startswith(query_lower)
    ):
        return 1
    return 2


@staff_required
def studio_user_search(request):
    """Staff-only JSON user search for Studio autocomplete fields.

    Searches across ``first_name``, ``last_name``, and ``email``
    (case-insensitive substring). Returns up to 10 results ordered by
    relevance (exact > startswith > substring) and then alphabetically by
    display name within each band.

    Each result includes ``id``, ``email``, ``first_name``, ``last_name``,
    ``display_name``, ``tier_level``, and ``has_community_access``. When the
    optional ``?sprint=<slug>`` query param is set, each result additionally
    reports ``in_sprint`` and ``has_plan_in_sprint`` flags for that sprint.
    An unknown sprint slug returns 404.
    """
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    sprint_slug = request.GET.get('sprint', '').strip()
    sprint = None
    if sprint_slug:
        try:
            sprint = Sprint.objects.get(slug=sprint_slug)
        except Sprint.DoesNotExist as exc:
            raise Http404(f'Sprint {sprint_slug!r} not found') from exc

    # The full query string is the primary substring match (so "alice" still
    # works the same way the old endpoint did). When the operator pastes a
    # full name like "Sam One", whitespace-separated tokens are also matched
    # against any field so the AND of tokens-must-each-appear-somewhere
    # still surfaces the right person -- the relevance ranker promotes the
    # exact full-name match to the top.
    tokens = [t for t in query.split() if t]
    name_or_email = (
        Q(email__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
    )
    for token in tokens:
        if token == query:
            continue
        name_or_email = name_or_email | (
            Q(email__icontains=token)
            | Q(first_name__icontains=token)
            | Q(last_name__icontains=token)
        )
    if query.isdigit():
        name_or_email = name_or_email | Q(pk=int(query))

    # Fetch a wider window than the cap so we can sort by relevance in Python
    # without paying for a second query. 50 is plenty given the cap of 10.
    candidates = list(
        User.objects
        .filter(name_or_email)
        .select_related('tier')
        [:50]
    )

    query_lower = query.lower()
    candidates.sort(
        key=lambda u: (
            _relevance_rank(u, query_lower),
            _display_name_for(u).lower(),
        )
    )
    candidates = candidates[:10]

    sprint_user_ids = set()
    plan_user_ids = set()
    if sprint is not None and candidates:
        candidate_ids = [u.pk for u in candidates]
        sprint_user_ids = set(
            SprintEnrollment.objects
            .filter(sprint=sprint, user_id__in=candidate_ids)
            .values_list('user_id', flat=True)
        )
        plan_user_ids = set(
            Plan.objects
            .filter(sprint=sprint, member_id__in=candidate_ids)
            .values_list('member_id', flat=True)
        )

    results = []
    for user in candidates:
        tier_level = user.tier.level if user.tier_id else 0
        row = {
            'id': user.pk,
            'email': user.email,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'display_name': _display_name_for(user),
            'tier_level': tier_level,
            'has_community_access': tier_level >= LEVEL_MAIN,
        }
        if sprint is not None:
            row['in_sprint'] = user.pk in sprint_user_ids
            row['has_plan_in_sprint'] = user.pk in plan_user_ids
        results.append(row)
    return JsonResponse({'results': results})


@staff_required
def legacy_tier_override_page_redirect(request):
    email = request.GET.get('email', '').strip()
    if email:
        user = User.objects.filter(email=email).only('pk').first()
        if user is not None:
            return HttpResponsePermanentRedirect(
                reverse('studio_user_tier_override_page', args=[user.pk])
            )
    return HttpResponsePermanentRedirect(reverse('studio_tier_overrides_list'))


@staff_required
def legacy_tier_override_create_redirect(request):
    email = (request.POST.get('email') or request.GET.get('email') or '').strip()
    user = User.objects.filter(email=email).only('pk').first()
    if user is not None:
        return _legacy_redirect(
            request,
            reverse('studio_user_tier_override_create', args=[user.pk]),
        )
    return _legacy_redirect(request, reverse('studio_tier_overrides_list'))


@staff_required
def legacy_tier_override_revoke_redirect(request):
    override_id = (
        request.POST.get('override_id') or request.GET.get('override_id') or ''
    ).strip()
    if override_id:
        override = TierOverride.objects.filter(pk=override_id).only('user_id').first()
        if override is not None:
            return _legacy_redirect(
                request,
                reverse('studio_user_tier_override_revoke', args=[override.user_id]),
            )
    return _legacy_redirect(request, reverse('studio_tier_overrides_list'))


@staff_required
def legacy_user_tier_override_action_redirect(request, user_id, action):
    return _legacy_redirect(
        request,
        reverse(f'studio_user_tier_override_{action}', args=[user_id]),
    )
