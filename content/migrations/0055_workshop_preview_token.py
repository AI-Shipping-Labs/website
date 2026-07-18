import uuid

from django.db import migrations, models


def populate_workshop_preview_tokens(apps, schema_editor):
    Workshop = apps.get_model("content", "Workshop")
    for workshop in Workshop.objects.filter(preview_token__isnull=True).iterator():
        workshop.preview_token = uuid.uuid4()
        workshop.save(update_fields=["preview_token"])


class Migration(migrations.Migration):
    dependencies = [("content", "0054_download_private_storage")]

    operations = [
        migrations.AddField(
            model_name="workshop",
            name="preview_token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(
            populate_workshop_preview_tokens,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="workshop",
            name="preview_token",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
