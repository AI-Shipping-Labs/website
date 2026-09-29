"""Email everyone interested in an event that its recap is ready.

The audience is the union of the event's registrants/attendees, members of
cohorts linked to the event's series, and readers of a linked book club
(``events.services.event_recap_notification.AUDIENCE_RESOLVERS``). Sends are
idempotent per event and user, so re-running never double-emails.

Usage::

    uv run python manage.py notify_recap_ready <event_id> --dry-run
    uv run python manage.py notify_recap_ready <event_id>
"""

from django.core.management.base import BaseCommand, CommandError

from events.models import Event
from events.services.event_recap_notification import (
    EventRecapNotReady,
    notify_recap_ready,
    preview_recap_audience,
)


def _format_reasons(reasons):
    return ", ".join(
        f"{item['source']} ({item['label']})" if item["label"] else item["source"]
        for item in reasons
    )


class Command(BaseCommand):
    help = (
        "Email the recap-ready notice to everyone interested in an event. "
        "Pass --dry-run to list the audience with reasons and send nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("event_id", type=int, help="Event primary key.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the audience and predicted statuses; send nothing.",
        )

    def handle(self, *args, **options):
        event = Event.objects.filter(pk=options["event_id"]).first()
        if event is None:
            raise CommandError(f"No event with id={options['event_id']}.")
        if options["dry_run"]:
            self._print_preview(preview_recap_audience(event))
            return
        try:
            result = notify_recap_ready(event)
        except EventRecapNotReady as exc:
            raise CommandError(f"Recap not ready ({exc.reason}): {exc}") from exc
        self._print_send(result)

    def _print_preview(self, result):
        self.stdout.write(f"DRY-RUN {result['event']['slug']} (id {result['event']['id']})")
        if not result["ready"]:
            self.stdout.write(f"Not ready ({result['reason_code']}): {result['reason']}")
        self.stdout.write(f"Recap URL: {result['recap_url']}")
        for item in result["results"]:
            self.stdout.write(
                f"  {item['user_id']}\t{item['email']}\t{item['email_status']}\t"
                f"{_format_reasons(item['reasons'])}"
            )
        self.stdout.write(
            f"eligible={result['eligible']} would_email={result['would_email']} "
            f"already_emailed={result['already_emailed']} skipped={result['skipped']} "
            f"by_reason={result['by_reason']}"
        )

    def _print_send(self, result):
        self.stdout.write(
            f"SENT {result['event']['slug']} (id {result['event']['id']}): "
            f"eligible={result['eligible']} emailed={result['emailed']} "
            f"notified={result['notified']} already_sent={result['already_sent']} "
            f"skipped={result['skipped']} failed={result['failed']} "
            f"by_reason={result['by_reason']}"
        )
        if result["failed"]:
            raise CommandError(f"{result['failed']} recipient(s) failed; re-run to retry.")
