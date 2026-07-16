"""Converge already-migrated development and fresh production on R1.

The migrations preceding this file were rewritten before production applied
them. Development applied their original bytes, so its physical schema can
have stricter columns, missing database defaults, and a removed plaintext
compatibility column even though Django now computes the edited R1 state.
This forward-only migration repairs physical drift without contract DDL.
"""

import copy
import uuid

from django.db import migrations, models
from django.db.models.fields import NOT_PROVIDED

DEFAULT_FIELDS = (
    ("accounts", "TierOverride", "source"),
    ("content", "Download", "storage_key"),
    ("content", "Download", "asset_mime_type"),
    ("content", "Download", "delivery_blocked_reason"),
    ("crm", "SlackChannelIngest", "known_threads_checked"),
    ("crm", "SlackChannelIngest", "advances_watermark"),
    ("crm", "SlackThread", "privacy_erased"),
    ("integrations", "MavenEnrollmentEvent", "course_key"),
    ("integrations", "MavenEnrollmentEvent", "cohort_key"),
    ("integrations", "MavenEnrollmentEvent", "identity_hash"),
    ("integrations", "MavenEnrollmentEvent", "lifecycle"),
    ("integrations", "MavenEnrollmentEvent", "welcome_eligible"),
    ("integrations", "MavenEnrollmentEvent", "override_status"),
    ("integrations", "MavenEnrollmentEvent", "override_attempts"),
    ("integrations", "MavenEnrollmentEvent", "override_error"),
    ("integrations", "MavenEnrollmentEvent", "slack_status"),
    ("integrations", "MavenEnrollmentEvent", "slack_attempts"),
    ("integrations", "MavenEnrollmentEvent", "slack_error"),
    ("integrations", "MavenEnrollmentEvent", "welcome_status"),
    ("integrations", "MavenEnrollmentEvent", "welcome_attempts"),
    ("integrations", "MavenEnrollmentEvent", "welcome_error"),
    ("integrations", "MavenEnrollmentEvent", "removal_status"),
    ("integrations", "MavenEnrollmentEvent", "removal_attempts"),
    ("integrations", "MavenEnrollmentEvent", "removal_error"),
    ("integrations", "MavenEnrollmentEvent", "updated_at"),
    ("integrations", "WebhookLog", "attempts"),
    ("integrations", "WebhookLog", "error_message"),
    ("plans", "Sprint", "audience"),
    ("plans", "Sprint", "description"),
    ("plans", "Sprint", "outcomes"),
    ("questionnaires", "OnboardingConversation", "turn_version"),
    ("triggers", "TriggerSubscription", "encrypted_secret"),
    ("triggers", "TriggerSubscription", "previous_encrypted_secret"),
    ("triggers", "TriggerSubscription", "secret_version"),
    ("triggers", "EventEmission", "occurred_at"),
    ("triggers", "EventEmission", "envelope"),
)


def _columns(schema_editor, model):
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, model._meta.db_table,
            )
        }


def _column_info(schema_editor, model):
    with schema_editor.connection.cursor() as cursor:
        return {
            column.name: column
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, model._meta.db_table,
            )
        }


def _constraints(schema_editor, model):
    with schema_editor.connection.cursor() as cursor:
        return schema_editor.connection.introspection.get_constraints(
            cursor, model._meta.db_table,
        )


def _clone_for_physical_state(field, **changes):
    clone = copy.copy(field)
    for name, value in changes.items():
        setattr(clone, name, value)
    return clone


def reconcile_physical_schema(apps, schema_editor):
    Subscription = apps.get_model("triggers", "TriggerSubscription")
    legacy_field = Subscription._meta.get_field("legacy_secret")
    if legacy_field.column not in _columns(schema_editor, Subscription):
        schema_editor.add_field(Subscription, legacy_field)

    for app_label, model_name, field_name in DEFAULT_FIELDS:
        Model = apps.get_model(app_label, model_name)
        field = Model._meta.get_field(field_name)
        old_field = _clone_for_physical_state(field, db_default=NOT_PROVIDED)
        schema_editor.alter_field(Model, old_field, field, strict=False)

    Event = apps.get_model("events", "Event")
    for field_name in ("calendar_uid", "host_access_version"):
        field = Event._meta.get_field(field_name)
        old_field = _clone_for_physical_state(field, null=False, blank=False)
        schema_editor.alter_field(Event, old_field, field, strict=False)

    BookedCall = apps.get_model("community", "BookedCall")
    UnmatchedBookedCall = apps.get_model("community", "UnmatchedBookedCall")
    for booked in BookedCall.objects.filter(host_id__isnull=True).iterator():
        UnmatchedBookedCall.objects.update_or_create(
            calendly_event_uri=booked.calendly_event_uri,
            defaults={
                "source_booked_call_id": booked.pk,
                "source_created_at": booked.created_at,
                "source_updated_at": booked.updated_at,
                "member_id": booked.member_id,
                "invitee_email": booked.invitee_email,
                "invitee_name": booked.invitee_name,
                "scheduled_at": booked.scheduled_at,
                "status": booked.status,
                "calendly_invitee_uri": booked.calendly_invitee_uri,
                "reschedule_url": booked.reschedule_url,
                "cancel_url": booked.cancel_url,
                "canceled_at": booked.canceled_at,
                "last_event_at": booked.last_event_at,
            },
        )
        booked.delete()

    host_field = BookedCall._meta.get_field("host")
    host_column = _column_info(schema_editor, BookedCall)[host_field.column]
    if host_column.null_ok:
        old_host = _clone_for_physical_state(
            host_field,
            null=True,
            blank=True,
            remote_field=host_field.remote_field,
        )
        schema_editor.alter_field(BookedCall, old_host, host_field, strict=False)

    SlackIngest = apps.get_model("crm", "SlackChannelIngest")
    slack_constraints = _constraints(schema_editor, SlackIngest)
    if "unique_running_slack_ingest_per_channel" in slack_constraints:
        schema_editor.remove_constraint(
            SlackIngest,
            models.UniqueConstraint(
                fields=("channel_id",),
                condition=models.Q(status="running"),
                name="unique_running_slack_ingest_per_channel",
            ),
        )

    MavenEvent = apps.get_model("integrations", "MavenEnrollmentEvent")
    maven_constraints = _constraints(schema_editor, MavenEvent)
    if "uniq_active_maven_occurrence" in maven_constraints:
        schema_editor.remove_constraint(
            MavenEvent,
            models.UniqueConstraint(
                fields=("identity_hash",),
                condition=models.Q(lifecycle="active"),
                name="uniq_active_maven_occurrence",
            ),
        )


def reconcile_legacy_writes(apps, schema_editor):
    from triggers.secrets import decrypt_secret, encrypt_secret

    Subscription = apps.get_model("triggers", "TriggerSubscription")
    for subscription in Subscription.objects.all().iterator():
        legacy = subscription.legacy_secret or ""
        encrypted = subscription.encrypted_secret or ""
        decrypted = ""
        if encrypted:
            decrypted = decrypt_secret(encrypted)
        if legacy and legacy != decrypted:
            subscription.encrypted_secret = encrypt_secret(legacy)
            subscription.secret_version = max(subscription.secret_version or 0, 1)
            subscription.save(update_fields=["encrypted_secret", "secret_version"])
        elif decrypted and not legacy:
            subscription.legacy_secret = decrypted
            subscription.save(update_fields=["legacy_secret"])

    Event = apps.get_model("events", "Event")
    for event in Event.objects.filter(calendar_uid__isnull=True).iterator():
        event.calendar_uid = f"event-{event.slug}@aishippinglabs.com"
        event.save(update_fields=["calendar_uid"])
    Event.objects.filter(host_access_version__isnull=True).update(
        host_access_version=uuid.uuid4(),
    )

    Emission = apps.get_model("triggers", "EventEmission")
    for emission in Emission.objects.select_related("user").filter(envelope={}).iterator():
        user = emission.user
        name = None
        if user is not None:
            name = " ".join(
                value for value in (user.first_name, user.last_name) if value
            ) or user.email
        emission.envelope = {
            "event": emission.event_name,
            "id": emission.envelope_id,
            "occurred_at": emission.occurred_at.isoformat(),
            "data": {
                "user_id": emission.user_id,
                "email": user.email if user is not None else None,
                "name": name,
                "min_level": (emission.properties or {}).get("min_level"),
                "properties": emission.properties or {},
            },
        }
        emission.save(update_fields=["envelope"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_alter_user_signup_source"),
        ("community", "0017_unmatchedbookedcall"),
        ("content", "0054_download_private_storage"),
        ("crm", "0008_slack_ingest_lease_and_refresh_count"),
        ("email_app", "0019_emaillog_dedupe_key"),
        ("events", "0042_event_host_access_version_hostinvitedelivery"),
        ("integrations", "0025_webhooklog_delivery_state"),
        ("payments", "0009_alter_paymentaccountmismatch_reason_and_more"),
        ("plans", "0029_sprint_audience_sprint_description_sprint_outcomes"),
        ("questionnaires", "0007_onboarding_turn_attempt"),
        ("triggers", "0002_secure_delivery_state"),
    ]

    operations = [
        migrations.RunPython(reconcile_physical_schema, migrations.RunPython.noop),
        migrations.RunPython(reconcile_legacy_writes, migrations.RunPython.noop),
    ]
