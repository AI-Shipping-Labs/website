"""Fail-closed deletion of recurring work that is incompatible with R1."""

from django.core.management.base import BaseCommand
from django_q.models import Schedule

from jobs.management.commands.setup_schedules import R2_ONLY_SCHEDULE_NAMES
from website.release_phase import background_work_enabled


class Command(BaseCommand):
    help = "Delete stale R2-only schedules before an R1 process starts serving"

    def handle(self, *args, **options):
        if background_work_enabled():
            return
        deleted, _ = Schedule.objects.filter(
            name__in=R2_ONLY_SCHEDULE_NAMES,
        ).delete()
        self.stdout.write(f"Suppressed {deleted} R1-incompatible schedule rows")
