"""Restore campaign 25 deliveries falsely marked sent with empty ses_message_id."""

from django.db import migrations
from django.db.models import Q

CAMPAIGN_25_ID = 25


def restore_campaign_25_false_sent_deliveries(apps, schema_editor):
    """Revert campaign 25 sent deliveries lacking an SES message ID back to pending."""
    CampaignDelivery = apps.get_model("email_app", "CampaignDelivery")
    EmailCampaign = apps.get_model("email_app", "EmailCampaign")
    EmailLog = apps.get_model("email_app", "EmailLog")

    false_sent = CampaignDelivery.objects.filter(
        campaign_id=CAMPAIGN_25_ID,
        state="sent",
        ses_message_id="",
    ).filter(Q(email_log__isnull=True) | Q(email_log__ses_message_id=""))

    rows = list(false_sent.values_list("pk", "email_log_id"))
    if not rows:
        return

    delivery_ids = []
    orphan_log_ids = []
    for pk, log_id in rows:
        delivery_ids.append(pk)
        if log_id is not None:
            orphan_log_ids.append(log_id)
    CampaignDelivery.objects.filter(pk__in=delivery_ids).update(
        state="pending",
        email_log=None,
        completed_at=None,
        resolution="",
        resolved_at=None,
    )
    if orphan_log_ids:
        EmailLog.objects.filter(
            campaign_id=CAMPAIGN_25_ID,
            pk__in=orphan_log_ids,
            ses_message_id="",
        ).delete()
    EmailCampaign.objects.filter(pk=CAMPAIGN_25_ID, status="sent").update(
        status="needs_attention",
    )


class Migration(migrations.Migration):
    dependencies = [("email_app", "0035_alter_emailcampaign_status")]

    operations = [
        migrations.RunPython(
            restore_campaign_25_false_sent_deliveries,
            migrations.RunPython.noop,
        ),
    ]
