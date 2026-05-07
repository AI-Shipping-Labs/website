"""Backfill direct user tiers from active Stripe subscriptions."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from payments.services.backfill_tiers import backfill_user_from_stripe
from payments.services.import_stripe import _price_to_tier_map

User = get_user_model()


class Command(BaseCommand):
    help = "Backfill user.tier from active Stripe subscriptions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report changes without writing users, overrides, or audit rows.",
        )
        parser.add_argument(
            "--email",
            help="Backfill one user by email instead of all users with Stripe customer IDs.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        email = options.get("email")
        queryset = User.objects.select_related("tier").exclude(stripe_customer_id="")

        if email:
            queryset = queryset.filter(email__iexact=email)
            if not queryset.exists():
                raise CommandError(f"No user with Stripe customer ID found for {email}.")

        price_to_tier = _price_to_tier_map()
        processed = changed = warnings = skipped = dry_runs = 0

        for user in queryset.order_by("email"):
            record = backfill_user_from_stripe(
                user,
                dry_run=dry_run,
                price_to_tier=price_to_tier,
            )
            processed += 1
            if record.status == "changed":
                changed += 1
            elif record.status == "warning":
                warnings += 1
            elif record.status == "dry_run":
                dry_runs += 1
            else:
                skipped += 1

            writer = self.stderr.write if record.status == "warning" else self.stdout.write
            writer(f"{record.email}: {record.message}")

        self.stdout.write(
            self.style.SUCCESS(
                "Processed "
                f"{processed}; changed={changed}; dry_run={dry_runs}; "
                f"skipped={skipped}; warnings={warnings}"
            )
        )
