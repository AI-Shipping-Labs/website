from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("content", "0059_instructor_email")]

    operations = [
        migrations.AddField(
            model_name="article",
            name="image_manifest",
            field=models.JSONField(
                blank=True,
                default=dict,
                editable=False,
                help_text=(
                    "Machine-owned responsive variants keyed by authoritative "
                    "image URL. Populated by content sync; never author-edited."
                ),
            ),
        ),
        migrations.AddField(
            model_name="article",
            name="image_manifest_complete",
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text=(
                    "Whether content sync has reconciled responsive image "
                    "variants. False keeps the unchanged-HEAD fast path "
                    "disabled until missing controlled variants have been "
                    "generated or classified."
                ),
            ),
        ),
    ]
