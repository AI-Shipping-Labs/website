"""Queue a Relay bulk campaign to the guarded internal/test recipient list."""

from django.core.management.base import BaseCommand, CommandError

from email_app.models import EmailCampaign
from email_app.services.relay_campaigns import (
    preview_native_test_campaign,
    queue_native_test_campaign,
)


class Command(BaseCommand):
    help = "Queue an AISHL Relay campaign to 200–500 configured internal/test recipients only."

    def add_arguments(self, parser):
        parser.add_argument("campaign_id", type=int)
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--confirm", metavar="CAMPAIGN_KEY")

    def handle(self, *args, campaign_id, dry_run, confirm, **options):
        try:
            campaign = EmailCampaign.objects.get(pk=campaign_id)
        except EmailCampaign.DoesNotExist as exc:
            raise CommandError(f"Campaign {campaign_id} does not exist.") from exc
        try:
            plan = preview_native_test_campaign(campaign, campaign_key=confirm)
        except Exception as exc:
            raise CommandError(f"Relay test campaign preflight failed: {exc}") from exc
        domain_counts = ", ".join(
            f"{domain}={count}" for domain, count in sorted(plan["domain_counts"].items())
        )
        if dry_run:
            self.stdout.write(
                f"campaign_key={plan['campaign_key']} recipient_count={plan['recipient_count']} "
                f"domain_counts={domain_counts}"
            )
            return
        try:
            result = queue_native_test_campaign(
                campaign,
                campaign_key=confirm,
            )
        except Exception as exc:
            raise CommandError(f"Relay test campaign was not queued: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(
            f"Queued {result['campaign_key']} for {result['recipient_count']} allowed test recipients."
        ))
        self.stdout.write(f"domain_counts={domain_counts}; recipient addresses were not printed.")
