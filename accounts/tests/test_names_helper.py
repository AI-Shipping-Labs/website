"""Tests for ``accounts.utils.names.set_name_from_external`` (issue #699).

Covers split rules, the do-not-overwrite invariant, the
combined-vs-pre-split precedence, whitespace handling, and the
"returns False on no-op" contract.
"""

from django.test import TestCase

from accounts.models import User
from accounts.utils.names import _split_full_name, set_name_from_external


class SplitFullNameTest(TestCase):
    """Pure-string split helper, no User involved."""

    def test_two_token_name(self):
        self.assertEqual(_split_full_name("Alex Grigorev"), ("Alex", "Grigorev"))

    def test_three_token_name_splits_on_last_whitespace(self):
        self.assertEqual(
            _split_full_name("Salvador Castillo Raya"),
            ("Salvador Castillo", "Raya"),
        )

    def test_single_token_goes_to_first_name(self):
        self.assertEqual(_split_full_name("Madonna"), ("Madonna", ""))

    def test_extra_whitespace_is_collapsed(self):
        self.assertEqual(
            _split_full_name("   Alex   Grigorev   "),
            ("Alex", "Grigorev"),
        )

    def test_empty_string_is_noop(self):
        self.assertEqual(_split_full_name(""), ("", ""))

    def test_whitespace_only_is_noop(self):
        self.assertEqual(_split_full_name("   "), ("", ""))


class SetNameFromExternalStripeTest(TestCase):
    """Stripe sends a single ``customer_details.name`` string."""

    def test_multi_token_fills_both_fields(self):
        user = User.objects.create_user(email="multi@test.com")
        changed = set_name_from_external(
            user, full_name="Salvador Castillo Raya", source="stripe",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Salvador Castillo")
        self.assertEqual(user.last_name, "Raya")

    def test_single_token_fills_first_name_only(self):
        user = User.objects.create_user(email="madonna@test.com")
        changed = set_name_from_external(
            user, full_name="Madonna", source="stripe",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Madonna")
        self.assertEqual(user.last_name, "")

    def test_strips_surrounding_whitespace(self):
        user = User.objects.create_user(email="ws@test.com")
        changed = set_name_from_external(
            user, full_name="   Alex  Grigorev   ", source="stripe",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_empty_string_is_noop(self):
        user = User.objects.create_user(email="empty@test.com")
        changed = set_name_from_external(user, full_name="", source="stripe")
        self.assertFalse(changed)
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")

    def test_missing_full_name_is_noop(self):
        user = User.objects.create_user(email="missing@test.com")
        changed = set_name_from_external(user, full_name=None, source="stripe")
        self.assertFalse(changed)


class SetNameFromExternalDoNotOverwriteTest(TestCase):
    """Once a field is non-empty, no source overwrites it."""

    def test_existing_first_name_is_preserved(self):
        user = User.objects.create_user(
            email="existing@test.com", first_name="Custom", last_name="",
        )
        changed = set_name_from_external(
            user, full_name="Other Name", source="stripe",
        )
        # Only last_name was empty, only it changes.
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Custom")
        self.assertEqual(user.last_name, "Name")

    def test_existing_last_name_is_preserved_with_pre_split(self):
        user = User.objects.create_user(
            email="prelast@test.com", first_name="", last_name="Smith",
        )
        changed = set_name_from_external(
            user, first="Alex", last="Grigorev", source="oauth:google",
        )
        # last_name="Smith" stays; first_name gets filled.
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Smith")

    def test_both_filled_is_noop(self):
        user = User.objects.create_user(
            email="both@test.com",
            first_name="Custom",
            last_name="Edit",
        )
        changed = set_name_from_external(
            user, full_name="Wrong Name", source="stripe",
        )
        self.assertFalse(changed)
        self.assertEqual(user.first_name, "Custom")
        self.assertEqual(user.last_name, "Edit")

    def test_whitespace_only_existing_value_is_treated_as_empty(self):
        user = User.objects.create_user(email="ws-existing@test.com")
        user.first_name = "   "
        user.last_name = "  "
        # Don't save — only assert in-memory behaviour against the
        # helper's contract.
        changed = set_name_from_external(
            user, full_name="Alex Grigorev", source="stripe",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")


class SetNameFromExternalPreSplitTest(TestCase):
    """Pre-split first / last from OIDC providers (Google, Slack)."""

    def test_google_style_fills_both(self):
        user = User.objects.create_user(email="g@test.com")
        changed = set_name_from_external(
            user, first="Alex", last="Grigorev", source="oauth:google",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_only_first_is_provided(self):
        user = User.objects.create_user(email="only-first@test.com")
        changed = set_name_from_external(
            user, first="Alex", last=None, source="oauth:google",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "")

    def test_only_last_is_provided(self):
        user = User.objects.create_user(email="only-last@test.com")
        changed = set_name_from_external(
            user, first=None, last="Grigorev", source="oauth:google",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "Grigorev")

    def test_pre_split_strips_whitespace(self):
        user = User.objects.create_user(email="ws-presplit@test.com")
        changed = set_name_from_external(
            user, first="  Alex  ", last="  Grigorev  ", source="oauth:google",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_pre_split_takes_precedence_over_full_name(self):
        """If a caller passes both, pre-split wins."""
        user = User.objects.create_user(email="pre-wins@test.com")
        changed = set_name_from_external(
            user,
            full_name="Wrong Combined",
            first="Alex",
            last="Grigorev",
            source="oauth:google",
        )
        self.assertTrue(changed)
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_pre_split_both_empty_is_noop(self):
        user = User.objects.create_user(email="empty-pre@test.com")
        changed = set_name_from_external(
            user, first="", last="", source="oauth:google",
        )
        self.assertFalse(changed)


class SetNameFromExternalLoggingTest(TestCase):
    """The helper emits a single INFO line on mutate with no PII."""

    def test_no_log_on_noop(self):
        user = User.objects.create_user(email="silent@test.com")
        with self.assertNoLogs("accounts.utils.names", level="INFO"):
            set_name_from_external(user, full_name="", source="stripe")

    def test_info_log_on_mutate_does_not_leak_name(self):
        user = User.objects.create_user(email="logged@test.com")
        with self.assertLogs("accounts.utils.names", level="INFO") as logs:
            set_name_from_external(
                user, full_name="Alex Grigorev", source="stripe",
            )
        # The name value must NOT appear in the log; only pk + source +
        # which fields were touched.
        joined = "\n".join(logs.output)
        self.assertNotIn("Alex", joined)
        self.assertNotIn("Grigorev", joined)
        self.assertIn("stripe", joined)
        self.assertIn(str(user.pk), joined)
        self.assertIn("first_name", joined)
        self.assertIn("last_name", joined)
