"""Restore the persistent subject sentinel for already-migrated cb4eb3d."""

import copy

from django.db import migrations
from django.db.models.fields import NOT_PROVIDED


def reconcile_subject_schema_and_data(apps, schema_editor):
    EmailLog = apps.get_model("email_app", "EmailLog")
    field = EmailLog._meta.get_field("subject")
    old_field = copy.copy(field)
    old_field.db_default = NOT_PROVIDED
    schema_editor.alter_field(EmailLog, old_field, field, strict=False)

    for log in EmailLog.objects.select_related("campaign").filter(subject="").iterator():
        if log.campaign_id and log.campaign.subject:
            log.subject = log.campaign.subject
            log.save(update_fields=["subject"])


class Migration(migrations.Migration):
    dependencies = [("email_app", "0020_emaillog_recipient_subject_snapshots")]

    operations = [
        migrations.RunPython(
            reconcile_subject_schema_and_data,
            migrations.RunPython.noop,
        ),
    ]
