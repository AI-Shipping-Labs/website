"""Hand ``ContactTag`` and ``AccountSession`` to ``accounts_ext`` (A3.2, #1692).

State-only. ``ContactTag`` keeps the ``accounts_contacttag`` table and
``AccountSession`` keeps the unmanaged ``django_session`` table, so the whole
migration is bookkeeping: the models leave the ``accounts`` label and
``User.contact_tags`` repoints at the relocated ``ContactTag`` without the
through table's foreign key changing target.

``User.contact_tags`` itself deliberately stays. It is the legacy half of the
A3.2a expand window: an old image rolling next to the new one keeps reading
and writing it. The contract unit removes it once the switch is deployed.

Reversible: reverting moves both models back under the ``accounts`` label.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0031_backfill_maven_webhook_signup_source"),
        ("accounts_ext", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="user",
                    name="contact_tags",
                    field=models.ManyToManyField(
                        blank=True,
                        help_text="Indexed membership relation mirroring the tags JSON payload.",
                        related_name="users",
                        to="accounts_ext.contacttag",
                    ),
                ),
                migrations.DeleteModel(name="ContactTag"),
                migrations.DeleteModel(name="AccountSession"),
            ],
        ),
    ]
