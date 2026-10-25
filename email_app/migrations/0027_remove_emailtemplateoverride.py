"""Remove local template overrides after a safe, key-only preflight."""

from django.db import migrations

LEGACY_SENDER_KEYS = (
    "SES_FROM_EMAIL",
    "SES_TRANSACTIONAL_FROM_EMAIL",
    "SES_PROMOTIONAL_FROM_EMAIL",
    "SES_WELCOME_FROM_EMAIL",
    "SES_WELCOME_REPLY_TO_EMAIL",
    "SES_UNSUBSCRIBE_EMAIL",
)


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


def remove_legacy_sender_settings(apps, schema_editor):
    IntegrationSetting = apps.get_model("integrations", "IntegrationSetting")
    Setting = apps.get_model("cb_config", "Setting")
    IntegrationSetting.objects.filter(key__in=LEGACY_SENDER_KEYS).delete()
    Setting.objects.filter(key__in=LEGACY_SENDER_KEYS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cb_config", "0001_initial"),
        ("email_app", "0026_relaytransactionaldelivery"),
        ("integrations", "0035_rename_relay_sender_settings"),
    ]

    operations = [
        migrations.RunPython(reject_nonempty_overrides, migrations.RunPython.noop),
        migrations.RunPython(remove_legacy_sender_settings, migrations.RunPython.noop),
        migrations.DeleteModel(name="EmailTemplateOverride"),
    ]
