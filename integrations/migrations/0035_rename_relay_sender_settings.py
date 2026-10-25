"""Preserve effective sender values under neutral Relay setting names."""

from django.db import migrations

RENAMES = {
    "SES_TRANSACTIONAL_FROM_EMAIL": "RELAY_TRANSACTIONAL_FROM_EMAIL",
    "SES_PROMOTIONAL_FROM_EMAIL": "RELAY_PROMOTIONAL_FROM_EMAIL",
    "SES_WELCOME_FROM_EMAIL": "RELAY_WELCOME_FROM_EMAIL",
    "SES_WELCOME_REPLY_TO_EMAIL": "RELAY_WELCOME_REPLY_TO_EMAIL",
    "SES_UNSUBSCRIBE_EMAIL": "RELAY_UNSUBSCRIBE_EMAIL",
}
GENERAL_SENDER_KEY = "SES_FROM_EMAIL"


def rename_sender_settings(apps, schema_editor):
    Donor = apps.get_model("integrations", "IntegrationSetting")
    Setting = apps.get_model("cb_config", "Setting")
    for old_key, new_key in RENAMES.items():
        value = _effective_value(Donor, Setting, new_key, old_key)
        if not value and old_key.endswith("_FROM_EMAIL"):
            value = _value(Donor, Setting, GENERAL_SENDER_KEY)
        _store_value(Donor, Setting, new_key, value)
    # Keep legacy rows until the email-app migration proves that no
    # unpublished local template overrides can abort this deploy. This makes
    # a rollback between the two migrations safe for the previous release.


def _effective_value(donor, setting, new_key, old_key):
    return _value(donor, setting, new_key) or _value(donor, setting, old_key)


def _value(donor, setting, key):
    package_value = setting.objects.filter(key=key).values_list("value", flat=True).first()
    if package_value:
        return package_value
    return donor.objects.filter(key=key).values_list("value", flat=True).first() or ""


def _store_value(donor, setting, key, value):
    if not value:
        return
    setting.objects.update_or_create(
        key=key,
        defaults={"value": value, "value_type": "str", "source": "db"},
    )
    donor.objects.update_or_create(
        key=key,
        defaults={
            "value": value,
            "is_secret": False,
            "group": "relay",
            "description": "Relay outbound mail setting",
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("cb_config", "0001_initial"),
        ("integrations", "0034_mavenenrollmentevent_tagging_attempted_at_and_more"),
    ]

    operations = [migrations.RunPython(rename_sender_settings, migrations.RunPython.noop)]
