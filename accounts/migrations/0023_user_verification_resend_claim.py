from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0022_privacyrequestlog")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="verification_resend_claimed_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Database time when the current verification-email resend "
                    "throttle window was claimed."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="verification_resend_claim_token",
            field=models.UUIDField(
                blank=True,
                editable=False,
                help_text=(
                    "Opaque operational token used to release only the "
                    "matching failed resend claim; not an email-verification "
                    "token."
                ),
                null=True,
            ),
        ),
    ]
