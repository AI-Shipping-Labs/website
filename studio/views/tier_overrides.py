"""Studio views for managing temporary tier overrides."""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

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


@staff_required
def tier_override_page(request):
    """Tier override management page with user search and override creation."""
    search_email = request.GET.get('email', '').strip()
    searched_user = None
    active_override = None
    override_history = []
    available_tiers = []
    is_highest_tier = False
    error_message = ''

    if search_email:
        try:
            searched_user = User.objects.select_related('tier').get(
                email=search_email,
            )
        except User.DoesNotExist:
            error_message = f'No user found with email "{search_email}".'

    if searched_user is not None:
        # Get active override
        active_override = (
            TierOverride.objects
            .filter(user=searched_user, is_active=True, expires_at__gt=timezone.now())
            .select_related('override_tier', 'granted_by')
            .first()
        )

        # Get override history (including inactive ones)
        override_history = (
            TierOverride.objects
            .filter(user=searched_user)
            .select_related('override_tier', 'original_tier', 'granted_by')
            .order_by('-created_at')
        )

        # Determine available tiers for override (only higher than subscription tier)
        current_level = searched_user.tier.level if searched_user.tier else 0
        highest_tier = Tier.objects.order_by('-level').first()
        if highest_tier and current_level >= highest_tier.level:
            is_highest_tier = True
        else:
            available_tiers = list(
                Tier.objects.filter(level__gt=current_level).order_by('level')
            )

    duration_labels = [label for label, _ in DURATION_CHOICES]

    return render(request, 'studio/tier_overrides.html', {
        'search_email': search_email,
        'searched_user': searched_user,
        'active_override': active_override,
        'override_history': override_history,
        'available_tiers': available_tiers,
        'is_highest_tier': is_highest_tier,
        'duration_labels': duration_labels,
        'error_message': error_message,
    })


@staff_required
@require_POST
def tier_override_create(request):
    """Create a new tier override for a user."""
    email = request.POST.get('email', '').strip()
    tier_id = request.POST.get('tier_id', '').strip()
    duration = request.POST.get('duration', '').strip()

    if not email or not tier_id or not duration:
        messages.error(request, 'Missing required fields.')
        return redirect(f'/studio/users/tier-override/?email={email}')

    try:
        user = User.objects.select_related('tier').get(email=email)
    except User.DoesNotExist:
        messages.error(request, f'No user found with email "{email}".')
        return redirect('/studio/users/tier-override/')

    try:
        override_tier = Tier.objects.get(pk=tier_id)
    except Tier.DoesNotExist:
        messages.error(request, 'Invalid tier selected.')
        return redirect(f'/studio/users/tier-override/?email={email}')

    # Calculate expires_at from duration
    now = timezone.now()
    expires_at = None
    for label, delta in DURATION_CHOICES:
        if label == duration:
            expires_at = now + delta
            break

    if expires_at is None:
        messages.error(request, f'Invalid duration: {duration}.')
        return redirect(f'/studio/users/tier-override/?email={email}')

    # Deactivate any existing active override for this user
    TierOverride.objects.filter(
        user=user,
        is_active=True,
    ).update(is_active=False)

    # Create new override
    TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=override_tier,
        expires_at=expires_at,
        granted_by=request.user,
        is_active=True,
    )

    messages.success(
        request,
        f'Override created: {user.email} -> {override_tier.name} '
        f'for {duration} (expires {expires_at.strftime("%Y-%m-%d %H:%M UTC")}).',
    )
    return redirect(f'/studio/users/tier-override/?email={email}')


@staff_required
@require_POST
def tier_override_revoke(request):
    """Revoke an active tier override."""
    override_id = request.POST.get('override_id', '').strip()
    email = request.POST.get('email', '').strip()

    if not override_id:
        messages.error(request, 'Missing override ID.')
        return redirect('/studio/users/tier-override/')

    try:
        override = TierOverride.objects.get(pk=override_id, is_active=True)
    except TierOverride.DoesNotExist:
        messages.error(request, 'Override not found or already inactive.')
        return redirect(f'/studio/users/tier-override/?email={email}')

    override.is_active = False
    override.save(update_fields=['is_active'])

    messages.success(request, f'Override revoked for {override.user.email}.')
    return redirect(f'/studio/users/tier-override/?email={email}')
