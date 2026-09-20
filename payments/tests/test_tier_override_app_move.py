"""TierOverride moved app label without moving its table (A3.2, #1692).

The move is SeparateDatabaseAndState: no row, primary key or index changed,
so a rolling deploy keeps both images on one physical table. These assertions
fail if a later change quietly rebuilds the table under a payments-owned name.
"""

from django.test import TestCase

from payments.models import TierOverride


class TierOverrideAppMoveTest(TestCase):
    def test_model_is_owned_by_payments_but_keeps_the_accounts_table(self):
        self.assertEqual(TierOverride._meta.app_label, "payments")
        self.assertEqual(TierOverride._meta.db_table, "accounts_tieroverride")

    def test_related_names_and_index_name_are_unchanged(self):
        self.assertEqual(
            TierOverride._meta.get_field("user").remote_field.related_name,
            "tier_overrides",
        )
        self.assertEqual(
            TierOverride._meta.get_field("granted_by").remote_field.related_name,
            "granted_overrides",
        )
        self.assertEqual(
            [index.name for index in TierOverride._meta.indexes],
            ["tieroverride_user_active_idx"],
        )

    def test_it_is_no_longer_importable_from_accounts(self):
        import accounts.models as accounts_models

        self.assertFalse(hasattr(accounts_models, "TierOverride"))
