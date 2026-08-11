from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from integrations.config import get_config
from payments.models import CheckoutAccountBinding, Tier
from payments.stripe_links import get_stripe_payment_links


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
