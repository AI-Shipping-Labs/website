import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.models.functions import Now

import triggers.destinations


def encrypt_existing_secrets(apps, schema_editor):
    from triggers.secrets import encrypt_secret

    Subscription = apps.get_model("triggers", "TriggerSubscription")
    for subscription in Subscription.objects.all().iterator():
        subscription.encrypted_secret = encrypt_secret(subscription.secret)
        subscription.save(update_fields=["encrypted_secret"])


def backfill_emission_envelopes(apps, schema_editor):
    Emission = apps.get_model("triggers", "EventEmission")
    for emission in Emission.objects.select_related("user").all().iterator():
        user = emission.user
        name = None
        if user is not None:
            name = " ".join(
                value for value in (user.first_name, user.last_name) if value
            ) or user.email
        emission.occurred_at = emission.created_at
        emission.envelope = {
            "event": emission.event_name,
            "id": emission.envelope_id,
            "occurred_at": emission.created_at.isoformat(),
            "data": {
                "user_id": emission.user_id,
                "email": user.email if user is not None else None,
                "name": name,
                "min_level": (emission.properties or {}).get("min_level"),
                "properties": emission.properties or {},
            },
        }
        emission.save(update_fields=["occurred_at", "envelope"])


class Migration(migrations.Migration):
    dependencies = [("triggers", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="triggersubscription",
            name="encrypted_secret",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="triggersubscription",
            name="previous_encrypted_secret",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="triggersubscription",
            name="secret_version",
            field=models.PositiveIntegerField(db_default=1, default=1),
        ),
        migrations.AddField(
            model_name="triggersubscription",
            name="previous_secret_valid_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(encrypt_existing_secrets, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="triggersubscription",
            name="encrypted_secret",
            field=models.TextField(blank=True, db_default="", default="", help_text="Encrypted HMAC signing secret. Never render this value."),
        ),
        migrations.AlterField(
            model_name="triggersubscription",
            name="secret",
            field=models.CharField(blank=True, help_text="R1 rollback-only plaintext compatibility shadow.", max_length=255, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField(
                    model_name="triggersubscription",
                    old_name="secret",
                    new_name="legacy_secret",
                ),
                migrations.AlterField(
                    model_name="triggersubscription",
                    name="legacy_secret",
                    field=models.CharField(blank=True, db_column="secret", editable=False, help_text="R1 rollback-only plaintext compatibility shadow.", max_length=255, null=True),
                ),
            ],
            database_operations=[],
        ),
        migrations.AlterField(
            model_name="triggersubscription",
            name="target_url",
            field=models.URLField(help_text="The external handler (e.g. a Lambda Function URL).", max_length=500, validators=[triggers.destinations.validate_outbound_url]),
        ),
        migrations.AddField(
            model_name="eventemission",
            name="occurred_at",
            field=models.DateTimeField(db_default=Now(), default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="eventemission",
            name="envelope",
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
        migrations.RunPython(backfill_emission_envelopes, migrations.RunPython.noop),
        migrations.CreateModel(
            name="WebhookDeliveryJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("target_url", models.URLField(max_length=500)),
                ("encrypted_secret", models.TextField()),
                ("secret_version", models.PositiveIntegerField()),
                ("request_body", models.TextField()),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("paused", "Paused"), ("succeeded", "Succeeded"), ("failed", "Failed")], default="pending", max_length=16)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=4)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("emission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="delivery_jobs", to="triggers.eventemission")),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="delivery_jobs", to="triggers.triggersubscription")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="webhookdeliveryjob",
            constraint=models.UniqueConstraint(fields=("emission", "subscription"), name="uniq_webhook_job_emission_subscription"),
        ),
        migrations.AddField(
            model_name="webhookdelivery",
            name="job",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="attempts", to="triggers.webhookdeliveryjob"),
        ),
        migrations.AddConstraint(
            model_name="webhookdelivery",
            constraint=models.UniqueConstraint(fields=("job", "attempt"), name="uniq_webhook_delivery_job_attempt"),
        ),
    ]
