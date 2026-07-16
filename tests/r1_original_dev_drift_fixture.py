"""Physical fingerprint produced by the original 1e5ab5b migration bytes.

The edited migration graph cannot reproduce development's already-applied DDL,
so migration tests call this fixture before applying the R1 reconciliation.
"""

import copy
from importlib import import_module

from django.db import models
from django.db.models.fields import NOT_PROVIDED

DEFAULT_FIELDS = import_module(
    "triggers.migrations.0003_r1_expand_reconciliation",
).DEFAULT_FIELDS


def _clone(field, **changes):
    result = copy.copy(field)
    for name, value in changes.items():
        setattr(result, name, value)
    return result


def apply_original_1e5_dev_drift(apps, schema_editor):
    """Mutate an edited-graph DB to the original target's physical shape."""

    for app_label, model_name, field_name in DEFAULT_FIELDS:
        Model = apps.get_model(app_label, model_name)
        field = Model._meta.get_field(field_name)
        schema_editor.alter_field(
            Model,
            field,
            _clone(field, db_default=NOT_PROVIDED),
            strict=False,
        )

    # Remove the compatibility column only after all subscription-column
    # rewrites. SQLite rebuilds whole tables for alter_field; doing another
    # rewrite with stale historical state after the removal can recreate the
    # column and corrupt the fixture's physical fingerprint.
    Subscription = apps.get_model("triggers", "TriggerSubscription")
    schema_editor.remove_field(
        Subscription,
        Subscription._meta.get_field("legacy_secret"),
    )

    Event = apps.get_model("events", "Event")
    for field_name in ("calendar_uid", "host_access_version"):
        field = Event._meta.get_field(field_name)
        schema_editor.alter_field(
            Event,
            field,
            _clone(field, null=False, blank=False),
            strict=False,
        )

    # The original community.0016 bytes made host nullable and did not create
    # the additive R1 staging table.
    BookedCall = apps.get_model("community", "BookedCall")
    host = BookedCall._meta.get_field("host")
    schema_editor.alter_field(
        BookedCall,
        host,
        _clone(host, null=True, blank=True),
        strict=False,
    )

    SlackIngest = apps.get_model("crm", "SlackChannelIngest")
    schema_editor.add_constraint(
        SlackIngest,
        models.UniqueConstraint(
            fields=("channel_id",),
            condition=models.Q(status="running"),
            name="unique_running_slack_ingest_per_channel",
        ),
    )
    Maven = apps.get_model("integrations", "MavenEnrollmentEvent")
    schema_editor.add_constraint(
        Maven,
        models.UniqueConstraint(
            fields=("identity_hash",),
            condition=models.Q(lifecycle="active"),
            name="uniq_active_maven_occurrence",
        ),
    )
