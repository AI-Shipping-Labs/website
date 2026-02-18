from django.conf import settings
from django.shortcuts import render

from payments.models import Tier


def pricing(request):
    """Pricing page showing all membership tiers in a comparison grid."""
    tiers = Tier.objects.all()

    stripe_links = settings.STRIPE_PAYMENT_LINKS

    tiers_data = []
    for tier in tiers:
        payment_links = stripe_links.get(tier.slug, {})
        tiers_data.append({
            "tier": tier,
            "payment_link_monthly": payment_links.get("monthly", "#"),
            "payment_link_annual": payment_links.get("annual", "#"),
        })

    context = {
        "tiers_data": tiers_data,
    }
    return render(request, "payments/pricing.html", context)
