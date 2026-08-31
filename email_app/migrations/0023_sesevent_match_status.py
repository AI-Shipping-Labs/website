from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("email_app", "0022_emailcampaign_audience_snapshotted_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sesevent",
            name="match_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("primary_email", "Matched by primary email"),
                    ("email_alias", "Matched by email alias"),
                    ("email_log", "Matched by send log"),
                    ("no_platform_account", "No platform account"),
                    ("unmatched_recipient", "Unmatched recipient"),
                    ("identity_conflict", "Identity conflict"),
                    (
                        "needs_reconciliation",
                        "Matched account · Needs reconciliation",
                    ),
                ],
                db_index=True,
                default="",
                db_default="",
                help_text=(
                    "How a bounce or complaint recipient was matched to a "
                    "canonical account. Blank for event types where recipient "
                    "identity was not evaluated and for historical rows "
                    "predating this field."
                ),
                max_length=32,
            ),
        ),
    ]
