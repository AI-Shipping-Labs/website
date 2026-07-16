from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.oauth_context import get_oauth_provider_context
from content.access import get_active_override
from integrations.config import get_config
from payments.models import CheckoutAccountBinding, Tier
from payments.stripe_links import get_stripe_payment_links
from payments.tier_state import build_tier_state


@ensure_csrf_cookie
def pricing(request):
    """Pricing page showing all membership tiers in a comparison grid."""
    tiers = Tier.objects.all()

    stripe_links = get_stripe_payment_links()

    user = request.user
    # Pricing account actions are subscription/base-tier aware by design:
    # temporary overrides are shown through ``build_tier_state`` below, but
    # checkout-vs-portal decisions follow the stored paid subscription.
    is_paid_member = (
        user.is_authenticated
        and user.tier is not None
        and user.tier.level > 0
    )
    active_override = get_active_override(user)

    locked_prefilled_email = ""
    if user.is_authenticated:
        locked_prefilled_email = user.email

    tiers_data = []
    for tier in tiers:
        payment_links = stripe_links.get(tier.slug, {})
        monthly_link = payment_links.get("monthly", "#")
        annual_link = payment_links.get("annual", "#")

        checkout_is_bound = bool(locked_prefilled_email and tier.level > 0)
        if checkout_is_bound:
            monthly_link = reverse(
                "checkout_binding_create",
                kwargs={"tier_slug": tier.slug, "billing_period": "monthly"},
            )
            annual_link = reverse(
                "checkout_binding_create",
                kwargs={"tier_slug": tier.slug, "billing_period": "annual"},
            )

        tiers_data.append({
            "tier": tier,
            "payment_link_monthly": monthly_link,
            "payment_link_annual": annual_link,
            "checkout_is_bound": checkout_is_bound,
            "state": build_tier_state(tier, user, active_override),
        })

    context = {
        "tiers_data": tiers_data,
        "stripe_checkout_enabled": False,
        "is_paid_member": is_paid_member,
        "prefilled_email": locked_prefilled_email,
        "stripe_customer_portal_url": get_config("STRIPE_CUSTOMER_PORTAL_URL", ""),
    }
    checkout_errors = {
        "temporarily_unavailable": (
            "Checkout is temporarily unavailable. Please choose a membership "
            "tier and try again later, or contact support."
        ),
        "invalid_interval": (
            "That billing interval is unavailable. Please choose monthly or "
            "annual billing for a membership tier, or contact support."
        ),
        "tier_unavailable": (
            "Checkout is not configured for that membership tier. Please choose "
            "another tier or contact support."
        ),
    }
    context["checkout_error"] = checkout_errors.get(
        request.GET.get("checkout_error", ""),
        "",
    )
    # Issue #652: anonymous visitors see the free-tier card render its
    # signup CTA as an inline register form. Pass the OAuth provider
    # flags and the round-trip URL so the inline partial picks up the
    # same context the standalone register page uses.
    if not user.is_authenticated:
        context.update(get_oauth_provider_context())
        context["next_url"] = request.path
        # Issue #653: the free-tier card always renders the inline
        # register form for anonymous visitors, so the footer newsletter
        # block would duplicate the signup CTA. Suppress the footer
        # block on /pricing for anonymous users; authenticated users
        # already never see it.
        context["hide_footer_newsletter"] = True
    return render(request, "payments/pricing.html", context)


def _binding_checkout_enabled():
    value = get_config("AUTHENTICATED_CHECKOUT_BINDING_ENABLED", "true")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _checkout_recovery_redirect(error_code):
    """Return members to the product UI with a safe, allowlisted error."""
    query = urlencode({"checkout_error": error_code})
    return redirect(f"{reverse('pricing')}?{query}#pricing-section")


@login_required
@require_POST
def create_checkout_binding(request, tier_slug, billing_period):
    """Issue an opaque checkout authorization and redirect to Stripe."""
    if not _binding_checkout_enabled():
        return _checkout_recovery_redirect("temporarily_unavailable")

    if billing_period not in {
        CheckoutAccountBinding.PERIOD_MONTHLY,
        CheckoutAccountBinding.PERIOD_ANNUAL,
    }:
        return _checkout_recovery_redirect("invalid_interval")

    tier = get_object_or_404(Tier, slug=tier_slug, level__gt=0)
    payment_link = (
        get_stripe_payment_links()
        .get(tier.slug, {})
        .get(billing_period, "")
    )
    if not payment_link or payment_link == "#":
        return _checkout_recovery_redirect("tier_unavailable")

    try:
        ttl_minutes = int(get_config("CHECKOUT_BINDING_TTL_MINUTES", "120"))
    except (TypeError, ValueError):
        ttl_minutes = 120
    ttl_minutes = min(max(ttl_minutes, 5), 1440)
    _binding, reference = CheckoutAccountBinding.issue(
        user=request.user,
        tier=tier,
        billing_period=billing_period,
        expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
    )
    query = urlencode({
        "client_reference_id": reference,
        "locked_prefilled_email": request.user.email,
    })
    separator = "&" if "?" in payment_link else "?"
    return redirect(f"{payment_link}{separator}{query}")
