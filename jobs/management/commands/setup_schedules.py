"""Register the complete declarative recurring schedule set."""

from django.core.management.base import BaseCommand

from jobs.schedule_reconciliation import (
    R2_ONLY_SCHEDULE_NAMES,
    apply_schedule_definitions,
    build_schedule_definitions,
)
from website.release_phase import background_work_enabled

__all__ = ["R2_ONLY_SCHEDULE_NAMES"]


class Command(BaseCommand):
    help = "Register default recurring job schedules"

    def handle(self, *args, **options):
        definitions = build_schedule_definitions(
            background_enabled=background_work_enabled(),
        )
        applied = apply_schedule_definitions(definitions)
        for definition in applied:
            description = f" ({definition.description})" if definition.description else ""
            self.stdout.write(self.style.SUCCESS(f"Registered: {definition.name}{description}"))
        self.stdout.write(self.style.SUCCESS("All default schedules registered."))
