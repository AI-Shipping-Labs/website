from django.db import migrations


def quarantine_legacy_sending_campaigns(apps, schema_editor):
    EmailCampaign = apps.get_model("email_app", "EmailCampaign")
    EmailCampaign.objects.filter(
        status="sending",
        audience_snapshotted_at__isnull=False,
        relay_campaign_state="",
    ).update(status="needs_attention")


class Migration(migrations.Migration):
    dependencies = [("email_app", "0032_campaignwave_relay_campaign_key_and_more")]

    operations = [
        migrations.RunPython(quarantine_legacy_sending_campaigns, migrations.RunPython.noop),
    ]
