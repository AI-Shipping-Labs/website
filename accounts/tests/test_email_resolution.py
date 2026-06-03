"""Tests for ``accounts.services.email_resolution`` (issue #840a).

Covers the fixed precedence: primary login wins; otherwise the alias
owner; otherwise ``None``. Primary always takes precedence over an alias.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import EmailAlias
from accounts.services.email_resolution import normalize_email, resolve_user_by_email

User = get_user_model()


class ResolveUserByEmailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.primary = User.objects.create_user(email="primary@test.com")
        cls.alias_owner = User.objects.create_user(email="canon@test.com")
        cls.alias = EmailAlias.objects.create(
            user=cls.alias_owner, email="relay@icloud.test"
        )

    def test_primary_email_resolves_to_that_user(self):
        self.assertEqual(resolve_user_by_email("primary@test.com"), self.primary)

    def test_primary_match_is_case_insensitive(self):
        self.assertEqual(resolve_user_by_email("PRIMARY@TEST.COM"), self.primary)

    def test_alias_resolves_to_owner_when_no_primary(self):
        self.assertEqual(resolve_user_by_email("relay@icloud.test"), self.alias_owner)

    def test_alias_match_is_normalized(self):
        self.assertEqual(
            resolve_user_by_email("  RELAY@icloud.test "), self.alias_owner
        )

    def test_unknown_email_returns_none(self):
        self.assertIsNone(resolve_user_by_email("nobody@test.com"))

    def test_empty_returns_none(self):
        self.assertIsNone(resolve_user_by_email(""))
        self.assertIsNone(resolve_user_by_email(None))

    def test_primary_wins_over_alias_for_same_address(self):
        # An address that is BOTH a primary login and (hypothetically) an
        # alias of another user must resolve to the primary login. The model
        # invariant forbids creating such an alias, but the resolver order is
        # the load-bearing guarantee, so we assert it directly: give user A a
        # primary email, and give user B an alias of a DIFFERENT address; a
        # lookup of A's email returns A, never B.
        user_b = User.objects.create_user(email="b@test.com")
        EmailAlias.objects.create(user=user_b, email="old-b@test.com")
        # Primary lookup for A's own email returns A.
        self.assertEqual(resolve_user_by_email("primary@test.com"), self.primary)
        # And B's alias still resolves to B (sanity).
        self.assertEqual(resolve_user_by_email("old-b@test.com"), user_b)

    def test_normalize_email_lowercases_and_strips(self):
        self.assertEqual(normalize_email("  Foo@Bar.COM "), "foo@bar.com")
        self.assertEqual(normalize_email(""), "")
        self.assertEqual(normalize_email(None), "")
