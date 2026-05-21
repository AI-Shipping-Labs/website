"""Audit-log tests for the User Management API writes (issue #764).

Every write endpoint (``PATCH /api/users/<email>``, ``POST .../tags``,
``DELETE .../tags/<tag>``) must append exactly one
``CommunityAuditLog`` row whose ``user`` FK is the SUBJECT user and
whose ``details`` text identifies the bearer via ``actor_token=<name>``.

No-op writes (e.g. ``PATCH unsubscribed=true`` on an already-unsubscribed
user) STILL produce an audit row -- attempting an action is itself
auditable.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from community.models import CommunityAuditLog

User = get_user_model()


class _UserAuditBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(
            user=cls.staff, name="ops-laptop",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _patch(self, email, payload):
        return self.client.patch(
            f"/api/users/{email}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _post_tag(self, email, payload):
        return self.client.post(
            f"/api/users/{email}/tags",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _delete_tag(self, email, tag):
        return self.client.delete(
            f"/api/users/{email}/tags/{tag}",
            **self._auth(),
        )


class PatchAuditLogTest(_UserAuditBase):
    def test_unsubscribe_writes_one_audit_row_with_actor_token(self):
        target = User.objects.create_user(email="alice@test.com", password=None)
        response = self._patch("alice@test.com", {"unsubscribed": True})
        self.assertEqual(response.status_code, 200)

        rows = CommunityAuditLog.objects.filter(user=target).order_by("id")
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_unsubscribe")
        self.assertEqual(row.user_id, target.id)
        # Actor must be encoded in details (the token's name).
        self.assertIn("actor_token=ops-laptop", row.details)
        # The previous state must be captured for context.
        self.assertIn("previous=False", row.details)

    def test_unsubscribe_idempotent_no_op_still_writes_audit_row(self):
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        target.unsubscribed = True
        target.save(update_fields=["unsubscribed"])

        response = self._patch("alice@test.com", {"unsubscribed": True})
        self.assertEqual(response.status_code, 200)

        rows = CommunityAuditLog.objects.filter(user=target).order_by("id")
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_unsubscribe")
        # The no-op annotation must be visible so operators can tell the
        # state didn't actually change.
        self.assertIn("no-op", row.details)
        self.assertIn("actor_token=ops-laptop", row.details)

    def test_email_verified_true_writes_api_verify_row(self):
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        target.email_verified = False
        target.save(update_fields=["email_verified"])

        response = self._patch("alice@test.com", {"email_verified": True})
        self.assertEqual(response.status_code, 200)

        rows = CommunityAuditLog.objects.filter(user=target)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_verify")
        self.assertIn("actor_token=ops-laptop", row.details)
        self.assertIn("previous_verified=False", row.details)

    def test_no_actor_token_name_falls_back_to_key_prefix(self):
        # A token without a ``name`` falls back to the masked
        # ``key_prefix`` Studio shows.
        nameless_token = Token.objects.create(user=self.staff, name="")
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        response = self.client.patch(
            "/api/users/alice@test.com",
            data=json.dumps({"unsubscribed": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {nameless_token.key}",
        )
        self.assertEqual(response.status_code, 200)
        row = CommunityAuditLog.objects.filter(user=target).first()
        # key_prefix is the first 8 chars + ellipsis.
        self.assertIn(
            f"actor_token={nameless_token.key_prefix}", row.details,
        )


class TagAuditLogTest(_UserAuditBase):
    def test_post_tag_writes_api_tag_row(self):
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        response = self._post_tag("alice@test.com", {"tag": "early-adopter"})
        self.assertEqual(response.status_code, 200)

        rows = CommunityAuditLog.objects.filter(user=target)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_tag")
        self.assertEqual(row.user_id, target.id)
        # The tag value AND actor must both be encoded.
        self.assertIn("'early-adopter'", row.details)
        self.assertIn("actor_token=ops-laptop", row.details)

    def test_post_tag_idempotent_still_writes_audit_row(self):
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        target.tags = ["early-adopter"]
        target.save(update_fields=["tags"])

        response = self._post_tag("alice@test.com", {"tag": "early-adopter"})
        self.assertEqual(response.status_code, 200)
        rows = CommunityAuditLog.objects.filter(user=target)
        # No-op POST still records the operator action.
        self.assertEqual(rows.count(), 1)
        self.assertIn("no-op", rows.first().details)

    def test_delete_tag_writes_api_tag_row(self):
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        target.tags = ["wave-2"]
        target.save(update_fields=["tags"])

        response = self._delete_tag("alice@test.com", "wave-2")
        self.assertEqual(response.status_code, 200)
        rows = CommunityAuditLog.objects.filter(user=target)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_tag")
        self.assertIn("'wave-2'", row.details)
        self.assertIn("actor_token=ops-laptop", row.details)

    def test_delete_tag_idempotent_still_writes_audit_row(self):
        target = User.objects.create_user(
            email="alice@test.com", password=None,
        )
        # Tag is NOT present initially.
        response = self._delete_tag("alice@test.com", "wave-2")
        self.assertEqual(response.status_code, 200)
        rows = CommunityAuditLog.objects.filter(user=target)
        self.assertEqual(rows.count(), 1)
        self.assertIn("no-op", rows.first().details)
