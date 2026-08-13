from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0013_alter_checkoutfulfillment_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="stripewebhookdeliveryattempt",
            name="stripe_charge_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="stripewebhookdeliveryattempt",
            name="stripe_dispute_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="stripewebhookdeliveryattempt",
            name="stripe_invoice_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="stripewebhookdeliveryattempt",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("received", "Received"),
                    ("processed", "Processed"),
                    ("already_processed", "Already processed"),
                    ("ignored_stale", "Ignored (stale subscription)"),
                    ("unmatched_user", "Unmatched user (retryable)"),
                    ("ambiguous_user", "Ambiguous user (terminal)"),
                    ("failed_transient", "Failed (transient)"),
                    ("failed_permanent", "Failed (permanent)"),
                    ("review_required", "Review required"),
                ],
                default="received",
                max_length=32,
            ),
        ),
    ]
