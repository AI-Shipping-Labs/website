"""Retry only the five failed calendar-registration deliveries in intake ca96488a."""

from django.core.management.base import BaseCommand, CommandError

from email_app.models import EmailLog, RelayTransactionalDelivery
from email_app.relay_transactional import RelayTransactionalError
from events.models import EventRegistration
from events.services.host_registration import is_host_registration
from events.services.registration_email import send_registration_confirmation

INCIDENT_REGISTRATIONS = {
    2064: (67, 125),
    2065: (67, 53),
    2066: (69, 53),
    2067: (70, 53),
    2068: (68, 53),
}
REQUIRED_APPROVAL_NOTE = "resend approved by Alexey"


def _idempotency_key(registration_id, event_id, user_id):
    return f"event-registration:{event_id}:{user_id}:{registration_id}"


def _validate_options(options):
    registration_ids = options["registration_ids"]
    if not registration_ids or len(set(registration_ids)) != len(registration_ids):
        raise CommandError("Provide unique incident registration IDs.")
    unknown_ids = sorted(set(registration_ids) - INCIDENT_REGISTRATIONS.keys())
    if unknown_ids:
        raise CommandError(f"Registration IDs outside this incident: {unknown_ids}")
    if options["send"] and options["approval_note"] != REQUIRED_APPROVAL_NOTE:
        raise CommandError(
            f'--send requires --approval-note "{REQUIRED_APPROVAL_NOTE}" '
            "after that exact note is present in intake ca96488a."
        )
    return sorted(registration_ids)


def _load_targets(registration_ids):
    registrations = EventRegistration.objects.filter(
        pk__in=registration_ids,
    ).select_related("event", "user").in_bulk()
    missing_ids = sorted(set(registration_ids) - registrations.keys())
    if missing_ids:
        raise CommandError(f"Incident registrations not found: {missing_ids}")
    keys = []
    for registration_id in registration_ids:
        event_id, user_id = INCIDENT_REGISTRATIONS[registration_id]
        keys.append(_idempotency_key(registration_id, event_id, user_id))
    deliveries = RelayTransactionalDelivery.objects.filter(
        idempotency_key__in=keys,
    ).in_bulk(field_name="idempotency_key")
    email_logs = set(EmailLog.objects.filter(
        dedupe_key__in=keys,
    ).values_list("dedupe_key", flat=True))
    return _validate_targets(registration_ids, registrations, deliveries, email_logs)


def _validate_targets(registration_ids, registrations, deliveries, email_logs):
    retry, accepted = [], []
    for registration_id in registration_ids:
        registration = registrations[registration_id]
        expected_event, expected_user = INCIDENT_REGISTRATIONS[registration_id]
        if (registration.event_id, registration.user_id) != (expected_event, expected_user):
            raise CommandError(f"Registration {registration_id} no longer matches the incident record.")
        if is_host_registration(registration):
            raise CommandError(f"Registration {registration_id} uses a host-specific idempotency key.")
        key = _idempotency_key(registration_id, expected_event, expected_user)
        delivery = deliveries.get(key)
        if delivery is None:
            raise CommandError(f"Delivery record missing for registration {registration_id}.")
        if delivery.status == RelayTransactionalDelivery.STATUS_ACCEPTED:
            accepted.append(registration_id)
            continue
        if (
            delivery.status != RelayTransactionalDelivery.STATUS_FAILED
            or delivery.error_code != "relay_http_error"
            or key in email_logs
        ):
            raise CommandError(f"Registration {registration_id} is not a retryable incident failure.")
        retry.append(registration)
    return retry, accepted


def _send_targets(registrations):
    sent, failures = [], []
    for registration in registrations:
        try:
            send_registration_confirmation(registration)
        except RelayTransactionalError as exc:
            failures.append((registration.pk, exc.code))
        else:
            sent.append(registration.pk)
    return sent, failures


class Command(BaseCommand):
    help = "Dry-run or idempotently retry the five failed event-registration emails from intake ca96488a."

    def add_arguments(self, parser):
        parser.add_argument(
            "--registration-ids",
            nargs="+",
            type=int,
            default=sorted(INCIDENT_REGISTRATIONS),
        )
        parser.add_argument("--send", action="store_true", help="submit retryable deliveries to Relay")
        parser.add_argument("--approval-note", default="")

    def handle(self, *args, **options):
        registration_ids = _validate_options(options)
        retry, accepted = _load_targets(registration_ids)
        self.stdout.write(f"registrations={len(registration_ids)} retryable={len(retry)} already_accepted={len(accepted)}")
        for registration in retry:
            key = _idempotency_key(registration.pk, registration.event_id, registration.user_id)
            self.stdout.write(f"registration={registration.pk} event={registration.event_id} key={key}")
        if not options["send"]:
            self.stdout.write("mode=dry_run; no messages submitted")
            return
        sent, failures = _send_targets(retry)
        self.stdout.write(f"sent={len(sent)} failed={len(failures)} already_accepted={len(accepted)}")
        if failures:
            failed_ids = [registration_id for registration_id, _ in failures]
            raise CommandError(f"Relay retry failed for registrations {failed_ids}; rerun the same command after resolving the error.")
