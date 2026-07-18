from django.db import migrations, models


def backfill_snapshots(apps, schema_editor):
    EmailLog = apps.get_model("email_app", "EmailLog")
    for log in EmailLog.objects.select_related("user", "campaign").iterator():
        changes = []
        if not log.recipient_email and log.user_id:
            log.recipient_email = log.user.email
            changes.append("recipient_email")
        if not log.subject and log.campaign_id:
            log.subject = log.campaign.subject
            changes.append("subject")
        if changes:
            log.save(update_fields=changes)


class Migration(migrations.Migration):
    dependencies = [("email_app", "0019_emaillog_dedupe_key")]

    operations = [
        migrations.AddField(
            model_name="emaillog",
            name="subject",
            field=models.CharField(
                blank=True,
                default="",
                db_default="",
                help_text="Immutable rendered subject passed to Amazon SES.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="emaillog",
            name="recipient_email",
            field=models.EmailField(
                blank=True,
                db_index=True,
                default="",
                help_text=(
                    "Immutable destination address used for this send, including "
                    "ordinary sends attached to a user."
                ),
                max_length=254,
            ),
        ),
        migrations.AlterField(
            model_name="emaillog",
            name="email_type",
            field=models.CharField(
                db_index=True,
                help_text=(
                    'Type of email: "campaign", "welcome", "payment_failed", '
                    '"cancellation", "community_invite", "lead_magnet_delivery", '
                    '"event_reminder", etc.'
                ),
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="emaillog",
            name="sent_at",
            field=models.DateTimeField(
                auto_now_add=True,
                db_index=True,
                help_text="When the email was sent.",
            ),
        ),
        migrations.RunPython(backfill_snapshots, migrations.RunPython.noop),
    ]
