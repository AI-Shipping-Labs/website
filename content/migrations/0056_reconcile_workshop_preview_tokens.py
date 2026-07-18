"""Converge the original cb4eb3d preview-token schema on safe R1 state."""

import copy
import uuid

from django.db import migrations


def reconcile_preview_token_schema_and_data(apps, schema_editor):
    Workshop = apps.get_model("content", "Workshop")
    field = Workshop._meta.get_field("preview_token")
    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name: column
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                Workshop._meta.db_table,
            )
        }
    if not columns[field.column].null_ok:
        old_field = copy.copy(field)
        old_field.null = False
        schema_editor.alter_field(Workshop, old_field, field, strict=False)

    for workshop in Workshop.objects.filter(preview_token__isnull=True).iterator():
        workshop.preview_token = uuid.uuid4()
        workshop.save(update_fields=["preview_token"])


class Migration(migrations.Migration):
    dependencies = [("content", "0055_workshop_preview_token")]

    operations = [
        migrations.RunPython(
            reconcile_preview_token_schema_and_data,
            migrations.RunPython.noop,
        ),
    ]
