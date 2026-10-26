"""Remove local template overrides from state after a key-only preflight.

The physical table and legacy sender rows remain for the previous application
revision during rolling deployment and rollback. A later contract release can
remove them after the old web and worker tasks have drained.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def reject_nonempty_overrides(apps, schema_editor):
    Override = apps.get_model("email_app", "EmailTemplateOverride")
    keys = list(
        Override.objects.exclude(subject="", body_markdown="", footer_note="")
        .order_by("template_name")
        .values_list("template_name", flat=True)
    )
    if keys:
        joined = ", ".join(keys)
        raise RuntimeError(
            "Local email template overrides must be published in Relay before removal. "
            f"Affected template keys: {joined}"
        )
    Override.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cb_config", "0001_initial"),
        ("email_app", "0026_relaytransactionaldelivery"),
        ("integrations", "0035_rename_relay_sender_settings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(reject_nonempty_overrides, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="emailtemplateoverride",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text="Last operator who saved this override.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EmailTemplateOverride"),
            ],
        ),
    ]
