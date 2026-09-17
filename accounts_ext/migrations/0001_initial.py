"""Create the site-owned extension schema (plan issue A3.2, #1692).

Three moves, two of them state-only:

- ``ContactTag`` changes app label from ``accounts`` to ``accounts_ext``. The
  table is pinned to ``accounts_contacttag``, so nothing is rebuilt and no row
  is touched: ``SeparateDatabaseAndState`` with an empty database half.
- ``AccountSession`` changes app label the same way. It is ``managed = False``
  over ``django_session``, so it would emit no SQL either way; the empty
  database half documents that.
- ``MemberExtra`` and its contact-tag through table are genuinely new and are
  created for real. ``MemberExtra`` is keyed on the user row, so its primary
  key equals the user id and ``accounts_ext.0002`` can copy the relation
  across without renumbering.

Reversible: reverting drops the two new tables and restores the two moved
models to the ``accounts`` label.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0029_privacycompletiondelivery_and_more"),
        ("sessions", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="ContactTag",
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
                        ("slug", models.CharField(max_length=255, unique=True)),
                    ],
                    options={
                        "db_table": "accounts_contacttag",
                        "ordering": ["slug"],
                    },
                ),
                migrations.CreateModel(
                    name="AccountSession",
                    fields=[
                        (
                            "session_key",
                            models.CharField(
                                max_length=40,
                                primary_key=True,
                                serialize=False,
                                verbose_name="session key",
                            ),
                        ),
                        ("session_data", models.TextField(verbose_name="session data")),
                        (
                            "expire_date",
                            models.DateTimeField(db_index=True, verbose_name="expire date"),
                        ),
                        (
                            "account_id",
                            models.PositiveBigIntegerField(blank=True, db_index=True, null=True),
                        ),
                    ],
                    options={
                        "verbose_name": "session",
                        "verbose_name_plural": "sessions",
                        "db_table": "django_session",
                        "abstract": False,
                        "managed": False,
                    },
                ),
            ],
        ),
        migrations.CreateModel(
            name="MemberExtra",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="member_extra",
                        serialize=False,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "contact_tags",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Indexed membership relation mirroring the tags JSON payload.",
                        related_name="member_extras",
                        to="accounts_ext.contacttag",
                    ),
                ),
            ],
            options={
                "verbose_name": "Member extra",
                "verbose_name_plural": "Member extras",
                "ordering": ["user_id"],
            },
        ),
    ]
