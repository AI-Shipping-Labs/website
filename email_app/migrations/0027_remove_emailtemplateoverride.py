"""Remove local template overrides from state after a key-only preflight.

The physical table and legacy sender rows remain for the previous application
revision during rolling deployment and rollback. A later contract release can
remove them after the old web and worker tasks have drained.
"""

from django.db import migrations


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
    ]

    operations = [
        migrations.RunPython(reject_nonempty_overrides, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EmailTemplateOverride"),
            ],
        ),
    ]
