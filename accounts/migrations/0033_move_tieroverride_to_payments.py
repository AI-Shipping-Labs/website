"""Release ``TierOverride`` to the ``payments`` label (A3.2, #1692).

State-only counterpart to ``payments.0021_tieroverride``. The table
``accounts_tieroverride`` is untouched: only the label the model is declared
under changes, so no row moves, no primary key is renumbered and no index is
rebuilt.

Reversible: reverting restores the model declaration under ``accounts``.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0032_move_contacttag_and_accountsession"),
        ("payments", "0021_tieroverride"),
        # triggers.0003 is a frozen R1 reconciliation that resolves
        # ("accounts", "TierOverride", "source") through the historical app
        # registry. Ordering it ahead of this state move keeps that lookup
        # valid on a fresh database; on a historical one it has long since run.
        ("triggers", "0003_r1_expand_reconciliation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="tieroverride",
                    name="tieroverride_user_active_idx",
                ),
                migrations.DeleteModel(name="TierOverride"),
            ],
        ),
    ]
