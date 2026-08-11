"""Public Membership journey composition."""

from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.oauth_context import get_oauth_provider_context
from content.models import Workshop
from content.tier_config import get_membership_benefits, get_tiers
from content.views.pages import _get_activity_sprints
from events.services.timeline import build_public_upcoming_timeline
from payments.services.membership_context import (
    build_membership_payment_context,
)


@ensure_csrf_cookie
def membership(request):
    """Render the canonical public Membership conversion journey."""
    user = request.user
    context = build_membership_payment_context(
        user,
        checkout_error_code=request.GET.get('checkout_error', ''),
    )
    configured_tiers = {
        item.get('stripe_key'): item
        for item in get_tiers()
        if item.get('stripe_key')
    }
    for item in context['tiers_data']:
        item['pricing_features'] = _membership_features(
            item['tier'],
            configured_tiers.get(item['tier'].slug),
        )

    context.update({
        'membership_benefits': [
            benefit
            for benefit in get_membership_benefits()
            if benefit['description']
        ],
        'activity_sprints': _get_activity_sprints(user),
        'recent_workshops': list(
            Workshop.objects.filter(status='published').order_by('-date')[:3]
        ),
    })
    context.update(build_public_upcoming_timeline(user, row_limit=1))

    if not user.is_authenticated:
        context.update(get_oauth_provider_context())
        context['next_url'] = request.path
        context['hide_footer_newsletter'] = True

    return render(request, 'content/membership/page.html', context)


def _membership_features(tier, configured_tier):
    """Return plan bullets from synced YAML, with legacy JSON fallback."""
    features = []
    inherited_tier = {'main': 'Basic', 'premium': 'Main'}.get(tier.slug)
    if inherited_tier:
        features.append({'title': f'Everything in {inherited_tier}'})

    if configured_tier is not None:
        configured_benefits = configured_tier.get('benefits')
        if configured_benefits is None:
            configured_benefits = configured_tier.get('activities', [])
        features.extend(
            {'title': str(benefit.get('title', '')).strip()}
            for benefit in configured_benefits
            if str(benefit.get('title', '')).strip()
        )
        return features

    for feature in tier.features:
        if isinstance(feature, dict):
            title = feature.get('title') or feature.get('text')
        else:
            title = str(feature)
        if not title or title == f'Everything in {inherited_tier}':
            continue
        features.append({'title': title})
    return features
