"""Studio views for managing temporary tier overrides."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import (
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
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


@staff_required
def studio_user_search(request):
    """Staff-only JSON user search endpoint for Studio autocomplete fields."""
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    qs = User.objects.filter(email__icontains=query)
    if query.isdigit():
        qs = User.objects.filter(Q(email__icontains=query) | Q(pk=int(query)))

    results = [
        {
            'id': user.pk,
            'email': user.email,
            'name': (user.get_full_name() or '').strip(),
        }
        for user in qs.order_by('email')[:10]
    ]
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
