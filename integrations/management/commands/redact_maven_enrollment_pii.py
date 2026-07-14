"""Redact retained Maven enrollment PII after the 30-day operations window."""

from django.core.management.base import BaseCommand

from jobs.tasks.cleanup import redact_old_maven_enrollment_pii


class Command(BaseCommand):
    help = "Redact Maven occurrence email/payload fields older than 30 days."

    def handle(self, *args, **options):
        result = redact_old_maven_enrollment_pii(days=30)
        self.stdout.write(self.style.SUCCESS(f"Redacted {result['redacted']} Maven occurrence(s)."))
