"""Dry run: who gets the 24h/20m reminders for one event, and with which link.

Sends nothing and writes nothing. The audience is the shared event audience
(registrants plus linked dated-cohort members while
``EVENT_REMINDERS_INCLUDE_COHORT`` is on).

Usage::

    uv run python manage.py preview_event_reminders <event_id>
    uv run python manage.py preview_event_reminders <event_id> --json
"""

import json

from django.core.management.base import BaseCommand, CommandError

from events.models import Event
from notifications.services.event_reminders import preview_reminder_audience


class Command(BaseCommand):
    help = (
        "List the 24h/20m reminder audience of one event with reasons, "
        "links and email status. Sends nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("event_id", type=int, help="Event primary key.")
        parser.add_argument(
            "--json", action="store_true", help="Print the preview as JSON.",
        )

    def handle(self, *args, **options):
        event = Event.objects.filter(pk=options["event_id"]).first()
        if event is None:
            raise CommandError(f"No event with id={options['event_id']}.")
        preview = preview_reminder_audience(event)
        if options["json"]:
            self.stdout.write(json.dumps(preview, indent=2))
            return
        self.stdout.write(
            f"DRY-RUN reminders for {preview['event']['slug']} "
            f"(id {preview['event']['id']}), starts {preview['start_datetime']}, "
            f"status {preview['status']}, include_cohort={preview['include_cohort']}",
        )
        for item in preview["results"]:
            reasons = ", ".join(
                f"{reason['source']} ({reason['label']})" if reason["label"]
                else reason["source"]
                for reason in item["reasons"]
            )
            already = ",".join(item["already_reminded"]) or "-"
            self.stdout.write(
                f"  {item['email']} [{reasons}] {item['email_status']} "
                f"already={already} link={item['link']}",
            )
        self.stdout.write(
            f"eligible={preview['eligible']} would_email={preview['would_email']}",
        )
