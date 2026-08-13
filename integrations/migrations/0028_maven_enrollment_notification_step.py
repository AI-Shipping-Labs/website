from django.db import migrations, models


def skip_existing_occurrences(apps, schema_editor):
    apps.get_model("integrations", "MavenEnrollmentEvent").objects.update(
        notification_status="skipped",
        notification_attempts=0,
        notification_attempted_at=None,
        notification_completed_at=None,
        notification_error="",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0027_reconcile_synclog_observability_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="mavenenrollmentevent",
            name="account_created",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="mavenenrollmentevent",
            name="notification_attempted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mavenenrollmentevent",
            name="notification_attempts",
            field=models.PositiveSmallIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="mavenenrollmentevent",
            name="notification_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mavenenrollmentevent",
            name="notification_error",
            field=models.CharField(blank=True, db_default="", default="", max_length=255),
        ),
        migrations.AddField(
            model_name="mavenenrollmentevent",
            name="notification_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped"),
                ],
                db_default="skipped",
                default="skipped",
                max_length=16,
            ),
        ),
        migrations.RunPython(skip_existing_occurrences, migrations.RunPython.noop),
    ]
