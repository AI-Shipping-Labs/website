"""Create Stripe products and prices for all paid tiers.

Creates a Stripe Product for each paid tier, with monthly and yearly
recurring prices. Saves the Stripe Price IDs back to the Tier model.

Usage:
    uv run python manage.py stripe_create_products
"""

import stripe
from django.core.management.base import BaseCommand

from integrations.config import get_config
from payments.models import Tier


class Command(BaseCommand):
    help = "Create Stripe products and prices for all paid tiers."

    def handle(self, *args, **options):
        secret_key = get_config("STRIPE_SECRET_KEY", "")
        if not secret_key:
            self.stderr.write("STRIPE_SECRET_KEY is not set.")
            return

        client = stripe.StripeClient(secret_key)

        paid_tiers = Tier.objects.exclude(slug="free").order_by("level")
        if not paid_tiers.exists():
            self.stdout.write("No paid tiers found.")
            return

        for tier in paid_tiers:
            # Skip if already has both price IDs
            if tier.stripe_price_id_monthly and tier.stripe_price_id_yearly:
                self.stdout.write(f"  {tier.name}: already has price IDs, skipping")
                continue

            # Create product
            product = client.products.create(params={
                "name": f"AI Shipping Labs - {tier.name}",
                "metadata": {"tier_slug": tier.slug, "tier_level": str(tier.level)},
            })
            self.stdout.write(f"  {tier.name}: created product {product.id}")

            # Create monthly price
            if tier.price_eur_month and not tier.stripe_price_id_monthly:
                monthly = client.prices.create(params={
                    "product": product.id,
                    "unit_amount": tier.price_eur_month * 100,  # cents
                    "currency": "eur",
                    "recurring": {"interval": "month"},
                    "metadata": {"tier_slug": tier.slug, "billing_period": "monthly"},
                })
                tier.stripe_price_id_monthly = monthly.id
                self.stdout.write(f"    monthly: {monthly.id} (EUR {tier.price_eur_month}/mo)")

            # Create yearly price
            if tier.price_eur_year and not tier.stripe_price_id_yearly:
                yearly = client.prices.create(params={
                    "product": product.id,
                    "unit_amount": tier.price_eur_year * 100,  # cents
                    "currency": "eur",
                    "recurring": {"interval": "year"},
                    "metadata": {"tier_slug": tier.slug, "billing_period": "yearly"},
                })
                tier.stripe_price_id_yearly = yearly.id
                self.stdout.write(f"    yearly:  {yearly.id} (EUR {tier.price_eur_year}/yr)")

            tier.save(update_fields=["stripe_price_id_monthly", "stripe_price_id_yearly"])

        self.stdout.write(self.style.SUCCESS("Done."))
