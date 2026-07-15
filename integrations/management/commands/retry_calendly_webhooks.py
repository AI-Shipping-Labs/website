from django.core.management.base import BaseCommand

from integrations.services.calendly_delivery import retry_failed_calendly_deliveries


class Command(BaseCommand):
    help = 'Retry durable failed/pending Calendly webhook deliveries.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        result = retry_failed_calendly_deliveries(limit=max(1, options['limit']))
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {result['processed']}; still failed {result['failed']}",
            ),
        )
