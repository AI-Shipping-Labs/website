"""Operator surface for the one-week onboarding reminder (issue #1133).

The reminder cohort is fully derivable from ``EmailLog`` (already exposed
in the admin and email tooling), so this feature intentionally ships no
new REST API. This command is the operator surface instead:

- ``--dry-run`` lists the members who WOULD be reminded right now (email,
  welcome ``sent_at``, days waiting) and sends nothing.
- A normal run invokes the sweep and prints the ``{sent, skipped}``
  summary.

Both paths honour the same Studio-editable settings the scheduled task
reads (``ONBOARDING_REMINDER_ENABLED`` / ``ONBOARDING_REMINDER_DELAY_DAYS``).
A dry run reports when the sweep is disabled so an operator is not misled
into thinking a manual run would send.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.tasks.remind_onboarding import (
    find_due_members,
    remind_onboarding_incomplete,
    reminder_delay_days,
    reminder_enabled,
)


class Command(BaseCommand):
    help = "Send (or preview) the one-week onboarding reminder to due members."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the members who would be reminded and send nothing.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self._dry_run()
            return

        summary = remind_onboarding_incomplete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Onboarding reminders: sent={summary['sent']} "
                f"skipped={summary['skipped']}"
            )
        )

    def _dry_run(self):
        if not reminder_enabled():
            self.stdout.write(
                self.style.WARNING(
                    "ONBOARDING_REMINDER_ENABLED is off — a real run would "
                    "send nothing."
                )
            )

        now = timezone.now()
        due = find_due_members(now=now)
        self.stdout.write(
            f"Reminder delay: {reminder_delay_days()} days. "
            f"Due members: {len(due)}."
        )
        for user, welcome_at in due:
            days_waiting = (now - welcome_at).days
            self.stdout.write(
                f"  {user.email} — welcome sent {welcome_at:%Y-%m-%d} "
                f"({days_waiting} days waiting)"
            )
        self.stdout.write(self.style.SUCCESS("Dry run complete — no email sent."))
