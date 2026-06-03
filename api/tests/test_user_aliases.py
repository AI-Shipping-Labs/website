"""Tests for the operator email-alias API (issue #840a).

Covers ``POST /api/users/<email>/aliases``,
``DELETE /api/users/<email>/aliases/<alias_email>``, the ``aliases`` field on
``GET /api/users/<email>``, every collision/idempotency rule, the audit rows,
and staff auth gating. Mirrors ``api/tests/test_tier_overrides_grant.py``.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import EmailAlias, Token
from community.models import CommunityAuditLog

User = get_user_model()


class UserAliasesApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="alias-bot")
        cls.non_staff = User.objects.create_user(
            email="plain@test.com", password="testpass", is_staff=False
        )
        # Construct directly to bypass the manager's staff-only validator
        # (models a legacy-demoted user), matching test_tier_overrides_grant.
        cls.non_staff_token = Token(
            key="non-staff-key-840", user=cls.non_staff, name="legacy"
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _auth(self, token=None):
        if token is False:
            return {}
        key = token.key if token is not None else self.token.key
        return {"HTTP_AUTHORIZATION": f"Token {key}"}

    def _post(self, email, payload, *, token=None):
        return self.client.post(
            f"/api/users/{email}/aliases",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _delete(self, email, alias_email, *, token=None):
        return self.client.delete(
            f"/api/users/{email}/aliases/{alias_email}",
            **self._auth(token),
        )

    def _get(self, email, *, token=None):
        return self.client.get(f"/api/users/{email}", **self._auth(token))

    # ---- Add + read ------------------------------------------------------

    def test_add_alias_returns_list_and_writes_audit(self):
        User.objects.create_user(email="canon@test.com")
        before = CommunityAuditLog.objects.filter(action="email_alias_added").count()

        response = self._post("canon@test.com", {"alias_email": "relay@x.test"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], "canon@test.com")
        self.assertEqual(body["aliases"], ["relay@x.test"])

        alias = EmailAlias.objects.get(email="relay@x.test")
        self.assertEqual(alias.user.email, "canon@test.com")
        self.assertEqual(alias.created_by, self.admin)

        added = CommunityAuditLog.objects.filter(action="email_alias_added")
        self.assertEqual(added.count(), before + 1)
        self.assertIn("alias-bot", added.latest("timestamp").details)

    def test_get_user_includes_aliases_array(self):
        owner = User.objects.create_user(email="canon@test.com")
        EmailAlias.objects.create(user=owner, email="relay@x.test")

        response = self._get("canon@test.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["aliases"], ["relay@x.test"])

    def test_add_alias_normalizes_input(self):
        User.objects.create_user(email="canon@test.com")
        response = self._post("canon@test.com", {"alias_email": "  RELAY@X.test "})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailAlias.objects.filter(email="relay@x.test").exists())

    # ---- Collision guards ------------------------------------------------

    def test_alias_that_is_a_primary_email_is_refused(self):
        User.objects.create_user(email="canon@test.com")
        User.objects.create_user(email="other@test.com")

        response = self._post("canon@test.com", {"alias_email": "other@test.com"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "alias_is_primary_email")
        self.assertFalse(EmailAlias.objects.filter(email="other@test.com").exists())

    def test_alias_owned_by_another_user_is_refused(self):
        user_a = User.objects.create_user(email="userA@test.com")
        User.objects.create_user(email="userB@test.com")
        EmailAlias.objects.create(user=user_a, email="relay@x.test")

        response = self._post("userB@test.com", {"alias_email": "relay@x.test"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "alias_taken")
        # Still points at user A.
        self.assertEqual(
            EmailAlias.objects.get(email="relay@x.test").user, user_a
        )

    def test_malformed_alias_email_returns_422(self):
        User.objects.create_user(email="canon@test.com")
        response = self._post("canon@test.com", {"alias_email": "not-an-email"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_email")

    def test_unknown_owner_returns_404(self):
        response = self._post("nobody@test.com", {"alias_email": "relay@x.test"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")

    # ---- Idempotency -----------------------------------------------------

    def test_readding_same_alias_is_idempotent(self):
        owner = User.objects.create_user(email="canon@test.com")
        EmailAlias.objects.create(user=owner, email="relay@x.test")

        response = self._post("canon@test.com", {"alias_email": "relay@x.test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            EmailAlias.objects.filter(email="relay@x.test").count(), 1
        )
        # The attempt is still audited.
        self.assertTrue(
            CommunityAuditLog.objects.filter(
                user=owner, action="email_alias_added"
            ).exists()
        )

    # ---- Remove ----------------------------------------------------------

    def test_remove_alias_then_remove_again_idempotent(self):
        owner = User.objects.create_user(email="canon@test.com")
        EmailAlias.objects.create(user=owner, email="relay@x.test")

        first = self._delete("canon@test.com", "relay@x.test")
        self.assertEqual(first.status_code, 200)
        self.assertFalse(EmailAlias.objects.filter(email="relay@x.test").exists())

        second = self._delete("canon@test.com", "relay@x.test")
        self.assertEqual(second.status_code, 200)

        removed = CommunityAuditLog.objects.filter(
            user=owner, action="email_alias_removed"
        )
        self.assertEqual(removed.count(), 2)

    def test_remove_unknown_owner_returns_404(self):
        response = self._delete("nobody@test.com", "relay@x.test")
        self.assertEqual(response.status_code, 404)

    # ---- Auth gating -----------------------------------------------------

    def test_add_without_token_returns_401(self):
        User.objects.create_user(email="canon@test.com")
        response = self._post(
            "canon@test.com", {"alias_email": "relay@x.test"}, token=False
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertFalse(EmailAlias.objects.filter(email="relay@x.test").exists())
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="email_alias_added").exists()
        )

    def test_add_with_non_staff_token_returns_401(self):
        User.objects.create_user(email="canon@test.com")
        response = self._post(
            "canon@test.com",
            {"alias_email": "relay@x.test"},
            token=self.non_staff_token,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertFalse(EmailAlias.objects.filter(email="relay@x.test").exists())
        self.assertFalse(
            CommunityAuditLog.objects.filter(action="email_alias_added").exists()
        )

    def test_remove_without_token_returns_401(self):
        owner = User.objects.create_user(email="canon@test.com")
        EmailAlias.objects.create(user=owner, email="relay@x.test")
        response = self._delete("canon@test.com", "relay@x.test", token=False)
        self.assertEqual(response.status_code, 401)
        self.assertTrue(EmailAlias.objects.filter(email="relay@x.test").exists())
