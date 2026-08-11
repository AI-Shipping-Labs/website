"""Payment-owned state exposed to the public Membership composition."""

from django.urls import reverse

from content.access import get_active_override
from integrations.config import get_config
from payments.models import Tier
from payments.stripe_links import get_stripe_payment_links
from payments.tier_state import build_tier_state

CHECKOUT_ERRORS = {
    'temporarily_unavailable': (
        'Checkout is temporarily unavailable. Please choose a membership '
        'tier and try again later, or contact support.'
    ),
    'invalid_interval': (
        'That billing interval is unavailable. Please choose monthly or '
        'annual billing for a membership tier, or contact support.'
    ),
    'tier_unavailable': (
        'Checkout is not configured for that membership tier. Please choose '
        'another tier or contact support.'
    ),
}


def build_membership_payment_context(user, *, checkout_error_code=''):
    """Return tier, subscription-action, and checkout context for Membership."""
    stripe_links = get_stripe_payment_links()
    active_override = get_active_override(user)
    prefilled_email = user.email if user.is_authenticated else ''
    is_paid_member = (
        user.is_authenticated
        and user.tier is not None
        and user.tier.level > 0
    )

    tiers_data = []
    for tier in Tier.objects.all():
        payment_links = stripe_links.get(tier.slug, {})
        monthly_link = payment_links.get('monthly', '#')
        annual_link = payment_links.get('annual', '#')
        tier_state = build_tier_state(tier, user, active_override)

        if (
            tier.slug == 'free'
            and tier_state['action_kind'] == 'disabled'
            and tier_state['action_label'] == 'Current plan'
        ):
            tier_state = {**tier_state, 'badge': '', 'note': ''}

        if (
            tier.slug == 'free'
            and user.is_authenticated
            and user.subscription_id
            and tier_state['action_label'] == 'Included'
        ):
            tier_state = _membership_state_without_helper_copy(
                tier_state,
                badge='',
                action_label='Downgrade',
                action_kind='portal',
            )
        elif tier_state['note'] in {
            'Included with every paid membership.',
            'Manage your subscription to switch to this tier.',
        }:
            tier_state = {**tier_state, 'note': ''}

        checkout_is_bound = bool(prefilled_email and tier.level > 0)
        if checkout_is_bound:
            monthly_link = reverse(
                'checkout_binding_create',
                kwargs={
                    'tier_slug': tier.slug,
                    'billing_period': 'monthly',
                },
            )
            annual_link = reverse(
                'checkout_binding_create',
                kwargs={
                    'tier_slug': tier.slug,
                    'billing_period': 'annual',
                },
            )

        tiers_data.append({
            'tier': tier,
            'payment_link_monthly': monthly_link,
            'payment_link_annual': annual_link,
            'checkout_is_bound': checkout_is_bound,
            'state': tier_state,
        })

    return {
        'tiers_data': tiers_data,
        'stripe_checkout_enabled': False,
        'is_paid_member': is_paid_member,
        'prefilled_email': prefilled_email,
        'stripe_customer_portal_url': get_config(
            'STRIPE_CUSTOMER_PORTAL_URL',
            '',
        ),
        'checkout_error': CHECKOUT_ERRORS.get(checkout_error_code, ''),
    }


def _membership_state_without_helper_copy(
    tier_state,
    *,
    badge,
    action_label,
    action_kind,
):
    return {
        **tier_state,
        'badge': badge,
        'note': '',
        'action_label': action_label,
        'action_kind': action_kind,
    }
