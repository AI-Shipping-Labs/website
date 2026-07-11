import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("payments", "0007_paymentaccountmismatch"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversionattribution",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The user who paid. Null after privacy deletion so "
                    "accounting attribution can be retained without a live "
                    "User row."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="conversion_attributions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="paymentaccountmismatch",
            name="paid_user",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "User who received entitlement from client_reference_id. "
                    "Null after privacy deletion."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payment_mismatches_as_paid_user",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
