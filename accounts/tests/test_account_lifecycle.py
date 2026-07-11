"""Tests for derived account-lifecycle reporting helpers."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.lifecycle import (
    account_lifecycle_q,
    derive_account_lifecycle,
)

User = get_user_model()


class AccountLifecycleHelperTest(TestCase):
    def test_unsubscribed_newsletter_origin_stays_newsletter_only(self):
        user = User.objects.create_user(
            email="newsletter@test.com",
            signup_source="newsletter",
            account_activated=False,
            unsubscribed=True,
        )
        self.assertEqual(derive_account_lifecycle(user), "newsletter_only")

    def test_activated_newsletter_origin_becomes_full_account(self):
        user = User.objects.create_user(
            email="activated@test.com",
            signup_source="newsletter",
            account_activated=True,
        )
        self.assertEqual(derive_account_lifecycle(user), "full_account")

    def test_imported_and_unknown_nonactivated_are_imported_or_unknown(self):
        imported = User.objects.create_user(
            email="imported@test.com",
            signup_source="imported",
            account_activated=False,
        )
        unknown = User.objects.create_user(
            email="unknown@test.com",
            signup_source="unknown",
            account_activated=False,
        )
        self.assertEqual(
            derive_account_lifecycle(imported),
            "imported_or_unknown",
        )
        self.assertEqual(
            derive_account_lifecycle(unknown),
            "imported_or_unknown",
        )

    def test_query_helper_matches_python_derivation(self):
        newsletter = User.objects.create_user(
            email="newsletter@test.com",
            signup_source="newsletter",
            account_activated=False,
        )
        full = User.objects.create_user(
            email="full@test.com",
            signup_source="signup",
            account_activated=False,
        )
        User.objects.create_user(
            email="imported@test.com",
            signup_source="imported",
            account_activated=False,
        )

        self.assertEqual(
            set(User.objects.filter(account_lifecycle_q("newsletter_only"))),
            {newsletter},
        )
        self.assertEqual(
            set(User.objects.filter(account_lifecycle_q("full_account"))),
            {full},
        )
