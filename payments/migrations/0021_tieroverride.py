"""Take ownership of ``TierOverride`` from the ``accounts`` label (A3.2, #1692).

State-only. The model keeps its ``accounts_tieroverride`` table, its rows,
its primary keys, its ``(user, is_active)`` index and both related names, so
there is nothing for the database half to do: a rolling deploy has old and new
images reading and writing the same physical table throughout, which a copy
between two tables could not offer.

Reversible: reverting removes the model from the ``payments`` state and
``accounts.0031`` puts it back under ``accounts``. No row is read or written
in either direction.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0020_alter_monthlypaymentgracedelivery_status"),
        ("accounts", "0030_move_contacttag_and_accountsession"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="TierOverride",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "expires_at",
                            models.DateTimeField(
                                help_text="When the override expires (UTC)."
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "is_active",
                            models.BooleanField(
                                default=True,
                                help_text="Set False on expiry or manual revocation.",
                            ),
                        ),
                        (
                            "source",
                            models.CharField(
                                blank=True,
                                db_default="",
                                db_index=True,
                                default="",
                                help_text=(
                                    "Stable grant source. Maven uses maven:<occurrence "
                                    "identity> so its entitlement can coexist with staff "
                                    "and billing grants."
                                ),
                                max_length=80,
                            ),
                        ),
                        (
                            "granted_by",
                            models.ForeignKey(
                                blank=True,
                                help_text="Admin who created the override.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="granted_overrides",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "original_tier",
                            models.ForeignKey(
                                blank=True,
                                help_text="User's tier when override was created (for audit).",
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="overrides_from",
                                to="payments.tier",
                            ),
                        ),
                        (
                            "override_tier",
                            models.ForeignKey(
                                help_text="The upgraded tier.",
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="overrides_to",
                                to="payments.tier",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="tier_overrides",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "accounts_tieroverride",
                        "ordering": ["-created_at"],
                        "indexes": [
                            models.Index(
                                fields=["user", "is_active"],
                                name="tieroverride_user_active_idx",
                            )
                        ],
                    },
                ),
            ],
        ),
    ]
