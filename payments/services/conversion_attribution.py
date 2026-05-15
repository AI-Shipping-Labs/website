"""Snapshot ``UserAttribution`` rows into frozen ``ConversionAttribution``.

This is the second idempotency layer beyond ``WebhookEvent``: we dedupe
on ``stripe_session_id`` so retries don't double-count a conversion.

Logger calls go through the ``payments.services`` package so tests that
patch ``payments.services.logger`` see the writes.
"""

from analytics.models import UserAttribution
from payments import services as _services
from payments.models import ConversionAttribution


def _record_conversion_attribution(
    user, session_data, tier, billing_period, amount_eur=None,
):
    """Snapshot a UserAttribution row into a frozen ConversionAttribution.

    Idempotent: if a row already exists for this stripe_session_id, return
    early without creating a duplicate. This is the second idempotency
    layer beyond ``WebhookEvent``.

    For users with no ``UserAttribution`` row (signed up before #194 or
    no UTMs ever captured), still create a row with all UTM fields blank
    so the conversion appears in dashboards as "no attribution".

    Args:
        user: The User who paid.
        session_data: The Stripe session payload dict.
        tier: The Tier purchased, or None for one-off course purchases.
        billing_period: "monthly", "yearly", or "" for one-off.
        amount_eur: Pre-computed amount for course purchases. If None and
            ``tier`` is set, derived from ``tier.price_eur_*`` based on
            ``billing_period``. ``mrr_eur`` is derived from ``amount_eur``
            and ``billing_period`` (yearly is divided by 12).
    """
    session_id = session_data.get("id", "")
    if not session_id:
        _services.logger.warning(
            "_record_conversion_attribution: empty session_id, skipping "
            "(user=%s)",
            user.email,
        )
        return

    # Belt-and-braces idempotency on top of WebhookEvent.
    if ConversionAttribution.objects.filter(stripe_session_id=session_id).exists():
        return

    # Compute amount_eur and mrr_eur for subscription-mode conversions.
    # For one-off course purchases the caller passes amount_eur in directly
    # and tier is None, so this block is skipped.
    mrr_eur = None
    if tier is not None and amount_eur is None:
        if billing_period == "monthly":
            amount_eur = tier.price_eur_month
            mrr_eur = tier.price_eur_month
        elif billing_period == "yearly":
            amount_eur = tier.price_eur_year
            if tier.price_eur_year is not None:
                mrr_eur = tier.price_eur_year // 12

    # Look up the user's attribution snapshot. Absence is allowed — we
    # still write a row with blank UTM fields so the dashboard shows the
    # conversion under "no attribution" rather than dropping it.
    attribution = None
    try:
        attribution = user.attribution
    except UserAttribution.DoesNotExist:
        attribution = None

    if attribution is None:
        _services.logger.info(
            "_record_conversion_attribution: no UserAttribution for user=%s, "
            "writing blank UTM snapshot (session=%s)",
            user.email,
            session_id,
        )

    fields = {
        "user": user,
        "stripe_session_id": session_id,
        "stripe_subscription_id": session_data.get("subscription") or "",
        "tier": tier,
        "billing_period": billing_period or "",
        "amount_eur": amount_eur,
        "mrr_eur": mrr_eur,
    }

    if attribution is not None:
        fields.update({
            "first_touch_utm_source": attribution.first_touch_utm_source,
            "first_touch_utm_medium": attribution.first_touch_utm_medium,
            "first_touch_utm_campaign": attribution.first_touch_utm_campaign,
            "first_touch_utm_content": attribution.first_touch_utm_content,
            "first_touch_utm_term": attribution.first_touch_utm_term,
            "first_touch_campaign": attribution.first_touch_campaign,
            "last_touch_utm_source": attribution.last_touch_utm_source,
            "last_touch_utm_medium": attribution.last_touch_utm_medium,
            "last_touch_utm_campaign": attribution.last_touch_utm_campaign,
            "last_touch_utm_content": attribution.last_touch_utm_content,
            "last_touch_utm_term": attribution.last_touch_utm_term,
            "last_touch_campaign": attribution.last_touch_campaign,
        })

    ConversionAttribution.objects.create(**fields)
