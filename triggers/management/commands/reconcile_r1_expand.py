"""Reconcile writes made by the production image against the R1 schema."""

import uuid

from django.core.management.base import BaseCommand

from events.models import Event
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import _identity
from triggers.dispatch import build_envelope
from triggers.models import EventEmission, TriggerSubscription
from triggers.secrets import decrypt_secret


def reconcile_r1_expand():
    """Repair compatibility sentinels idempotently before worker handoff."""

    counts = {"subscriptions": 0, "events": 0, "emissions": 0, "maven": 0}

    for subscription in TriggerSubscription.objects.all().iterator():
        legacy = subscription.legacy_secret or ""
        decrypted = (
            decrypt_secret(subscription.encrypted_secret)
            if subscription.encrypted_secret
            else ""
        )
        if legacy and legacy != decrypted:
            subscription.set_secret(legacy)
            subscription.save(update_fields=[
                "encrypted_secret",
                "legacy_secret",
                "previous_encrypted_secret",
                "previous_secret_valid_until",
                "secret_version",
                "updated_at",
            ])
            counts["subscriptions"] += 1
        elif decrypted and not legacy:
            subscription.legacy_secret = decrypted
            subscription.save(update_fields=["legacy_secret", "updated_at"])
            counts["subscriptions"] += 1

    for event in Event.objects.filter(calendar_uid__isnull=True).iterator():
        event.calendar_uid = f"event-{event.slug}@aishippinglabs.com"
        event.save(update_fields=["calendar_uid", "updated_at"])
        counts["events"] += 1
    for event in Event.objects.filter(host_access_version__isnull=True).iterator():
        event.host_access_version = uuid.uuid4()
        event.save(update_fields=["host_access_version", "updated_at"])
        counts["events"] += 1

    for emission in EventEmission.objects.select_related("user").filter(envelope={}).iterator():
        emission.envelope = build_envelope(
            emission.event_name,
            emission.user,
            emission.properties,
            envelope_id=emission.envelope_id,
            min_level=(emission.properties or {}).get("min_level"),
            occurred_at=emission.occurred_at,
        )
        emission.save(update_fields=["envelope"])
        counts["emissions"] += 1

    for row in MavenEnrollmentEvent.objects.filter(
        lifecycle=MavenEnrollmentEvent.LIFECYCLE_LEGACY,
        identity_hash="",
    ).iterator():
        row.course_key = row.course_key or row.course
        row.cohort_key = row.cohort_key or row.cohort
        row.identity_hash = _identity(row.email, row.course_key, row.cohort_key)
        row.save(update_fields=["course_key", "cohort_key", "identity_hash", "updated_at"])
        counts["maven"] += 1

    return counts


class Command(BaseCommand):
    help = "Idempotently reconcile production-image writes in the R1 expand schema"

    def handle(self, *args, **options):
        counts = reconcile_r1_expand()
        self.stdout.write(
            "R1 compatibility reconciliation complete: "
            + ", ".join(f"{name}={count}" for name, count in counts.items()),
        )
