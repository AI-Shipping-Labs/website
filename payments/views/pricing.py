from django.conf import settings
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from payments.models import Tier


@ensure_csrf_cookie
def pricing(request):
    """Pricing page showing all membership tiers in a comparison grid."""
    tiers = Tier.objects.all()

    stripe_checkout_enabled = settings.STRIPE_CHECKOUT_ENABLED
    stripe_links = settings.STRIPE_PAYMENT_LINKS

    # Determine if user is a paid member
    user = request.user
    is_paid_member = (
        user.is_authenticated
        and user.tier is not None
        and user.tier.level > 0
    )

    # Build prefilled_email suffix for payment links
    prefilled_email = ""
    if not stripe_checkout_enabled and user.is_authenticated:
        prefilled_email = user.email

    tiers_data = []
    for tier in tiers:
        payment_links = stripe_links.get(tier.slug, {})
        monthly_link = payment_links.get("monthly", "#")
        annual_link = payment_links.get("annual", "#")

        # Append prefilled_email to payment links for logged-in users
        if prefilled_email and not stripe_checkout_enabled:
            if monthly_link and monthly_link != "#":
                sep = "&" if "?" in monthly_link else "?"
                monthly_link = f"{monthly_link}{sep}prefilled_email={prefilled_email}"
            if annual_link and annual_link != "#":
                sep = "&" if "?" in annual_link else "?"
                annual_link = f"{annual_link}{sep}prefilled_email={prefilled_email}"

        tiers_data.append({
            "tier": tier,
            "payment_link_monthly": monthly_link,
            "payment_link_annual": annual_link,
        })

    context = {
        "tiers_data": tiers_data,
        "stripe_checkout_enabled": stripe_checkout_enabled,
        "is_paid_member": is_paid_member,
        "prefilled_email": prefilled_email,
        "stripe_customer_portal_url": settings.STRIPE_CUSTOMER_PORTAL_URL,
    }
    return render(request, "payments/pricing.html", context)
