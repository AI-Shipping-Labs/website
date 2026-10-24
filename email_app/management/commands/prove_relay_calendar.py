"""Send a fixed-recipient Relay calendar proof without production fixtures."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from accounts.services.timezones import format_user_datetime
from email_app.relay_transactional import (
    RelayTransactionalError,
    calendar_alternative,
    recipient_template_context,
    send_relay_transactional,
)
from events.models import Event
from events.services.calendar_invite import AUDIENCE_ATTENDEE, generate_ics
from events.services.calendar_links import build_calendar_links
from integrations.config import site_base_url

RECIPIENT_EMAIL = "alexey.s.grigoriev@gmail.com"
TEMPLATE_KEY = "event_registration"
DEFAULT_IDEMPOTENCY_KEY = "proof:relay-calendar:issue-2006:v1"
PROOF_EVENT_START = datetime(2035, 1, 15, 17, 0, tzinfo=UTC)
PROOF_EVENT_UID = "relay-calendar-proof-2006@aishippinglabs.com"
SAFE_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,199}")


def _proof_event():
    return Event(
        slug="relay-calendar-proof",
        title="Relay calendar delivery proof",
        description="A synthetic event used to verify Relay calendar delivery.",
        start_datetime=PROOF_EVENT_START,
        end_datetime=PROOF_EVENT_START + timedelta(hours=1),
        timezone="Europe/Berlin",
        ics_sequence=1,
        calendar_uid=PROOF_EVENT_UID,
    )


def _proof_recipient():
    return SimpleNamespace(
        email=RECIPIENT_EMAIL,
        first_name="Alexey",
        last_name="",
        preferred_timezone="Europe/Berlin",
    )


def _proof_context(event, recipient):
    base_url = site_base_url()
    links = build_calendar_links(event)
    context = {
        "event_title": event.title,
        "event_datetime": format_user_datetime(event.start_datetime, recipient),
        "timezone_help": "Time shown in Europe/Berlin.",
        "join_url": f"{base_url}/events/{event.slug}/join",
        "cancel_url": f"{base_url}/events",
        "google_calendar_url": links["google"],
        "outlook_calendar_url": links["outlook"],
        "office365_calendar_url": links["office365"],
        "is_host_registration": True,
    }
    return recipient_template_context(recipient, context)


def _validate_key(value):
    if SAFE_KEY_PATTERN.fullmatch(value):
        return value
    raise CommandError("--key must contain only letters, digits, colons, underscores, or hyphens")


def _output_line(status, digest, message_id=""):
    fields = [f"status={status}", f"template={TEMPLATE_KEY}"]
    if message_id:
        fields.append(f"message_id={message_id}")
    fields.append(f"calendar_sha256={digest}")
    return " ".join(fields)


class Command(BaseCommand):
    help = "Build or opt in to sending the fixed-recipient Relay calendar proof."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send",
            action="store_true",
            help="submit the proof to Relay; the default is a network-free dry run",
        )
        parser.add_argument(
            "--key",
            default=DEFAULT_IDEMPOTENCY_KEY,
            help="stable idempotency key for this proof run",
        )

    def handle(self, *args, **options):
        idempotency_key = _validate_key(options["key"])
        event = _proof_event()
        recipient = _proof_recipient()
        ics_content = generate_ics(
            event,
            method="REQUEST",
            audience=AUDIENCE_ATTENDEE,
            attendee_email=RECIPIENT_EMAIL,
        )
        digest = hashlib.sha256(ics_content).hexdigest()
        if not options["send"]:
            self.stdout.write(_output_line("dry_run", digest))
            return
        try:
            result = send_relay_transactional(
                RECIPIENT_EMAIL,
                TEMPLATE_KEY,
                _proof_context(event, recipient),
                idempotency_key=idempotency_key,
                safe_correlation="relay_calendar_proof=issue-2006",
                calendar=calendar_alternative(ics_content, method="REQUEST"),
            )
        except RelayTransactionalError as exc:
            raise CommandError(f"Relay proof failed: {exc.code}") from exc
        self.stdout.write(_output_line(result.status, digest, result.message_id))
