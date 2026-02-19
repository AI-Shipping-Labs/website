"""Tests for the CommunityAuditLog model."""

from django.test import TestCase

from accounts.models import User
from community.models import CommunityAuditLog


class CommunityAuditLogModelTest(TestCase):
    """Tests for CommunityAuditLog creation and fields."""

    def setUp(self):
        self.user = User.objects.create_user(email="audit@test.com")

    def test_create_audit_log_invite(self):
        log = CommunityAuditLog.objects.create(
            user=self.user,
            action="invite",
            details='{"slack_user_id": "U123"}',
        )
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.action, "invite")
        self.assertIn("U123", log.details)
        self.assertIsNotNone(log.timestamp)

    def test_create_audit_log_remove(self):
        log = CommunityAuditLog.objects.create(
            user=self.user,
            action="remove",
            details='{"reason": "downgrade"}',
        )
        self.assertEqual(log.action, "remove")

    def test_create_audit_log_reactivate(self):
        log = CommunityAuditLog.objects.create(
            user=self.user,
            action="reactivate",
            details='{"slack_user_id": "U456"}',
        )
        self.assertEqual(log.action, "reactivate")

    def test_create_audit_log_link(self):
        log = CommunityAuditLog.objects.create(
            user=self.user,
            action="link",
            details='{"source": "email_matcher"}',
        )
        self.assertEqual(log.action, "link")

    def test_str_representation(self):
        log = CommunityAuditLog.objects.create(
            user=self.user,
            action="invite",
        )
        self.assertIn("invite", str(log))
        self.assertIn("audit@test.com", str(log))

    def test_ordering_is_newest_first(self):
        log1 = CommunityAuditLog.objects.create(user=self.user, action="invite")
        log2 = CommunityAuditLog.objects.create(user=self.user, action="remove")
        logs = list(CommunityAuditLog.objects.all())
        self.assertEqual(logs[0], log2)
        self.assertEqual(logs[1], log1)

    def test_details_default_empty(self):
        log = CommunityAuditLog.objects.create(
            user=self.user,
            action="invite",
        )
        self.assertEqual(log.details, "")

    def test_cascade_delete_user(self):
        CommunityAuditLog.objects.create(user=self.user, action="invite")
        self.assertEqual(CommunityAuditLog.objects.count(), 1)
        self.user.delete()
        self.assertEqual(CommunityAuditLog.objects.count(), 0)
