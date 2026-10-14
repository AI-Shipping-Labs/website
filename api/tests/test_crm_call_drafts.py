"""Tests for ``GET /api/crm/call-email-drafts`` (issue #1951).

Read-only staff listing of the attendee summary email drafts the
booking-call capture pipeline stores. Nothing here can send email: the
endpoint is GET-only and the only send path is the Studio staff button.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from crm.services.calls import record_call

User = get_user_model()


class CallEmailDraftsEndpointTest(TestCase):
    URL = "/api/crm/call-email-drafts"

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="drafts-staff@test.com", password="pw", is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="ops")
        # Token creation requires a staff owner (the model enforces
        # staff at save time); create then demote like the other suites.
        cls.member = User.objects.create_user(
            email="drafts-member@test.com", password="pw", is_staff=True,
        )
        cls.member_token = Token.objects.create(user=cls.member, name="m")
        cls.member.is_staff = False
        cls.member.save(update_fields=["is_staff"])

        cls.drafted = record_call(
            email="attendee-a@test.com",
            occurred_at=timezone.now() - datetime.timedelta(hours=3),
            summary="Booked: portfolio review.",
            source="booking",
            source_ref="BK-DRAFT-1",
        )
        cls.drafted.email_draft_subject = "Your 1:1 with Alexey"
        cls.drafted.email_draft_body = "Great talking!"
        cls.drafted.email_draft_ready_at = timezone.now()
        cls.drafted.save()

        cls.sent = record_call(
            email="attendee-b@test.com",
            occurred_at=timezone.now() - datetime.timedelta(hours=2),
            summary="Booked: career review.",
            source="booking",
            source_ref="BK-DRAFT-2",
        )
        cls.sent.email_draft_subject = "Your 1:1 with Alexey"
        cls.sent.email_draft_body = "Great talking!"
        cls.sent.email_draft_ready_at = timezone.now()
        cls.sent.email_sent_at = timezone.now()
        cls.sent.email_sent_by = cls.staff
        cls.sent.save()

        cls.dismissed = record_call(
            email="attendee-c@test.com",
            occurred_at=timezone.now() - datetime.timedelta(hours=1),
            summary="Booked: mentoring.",
            source="booking",
            source_ref="BK-DRAFT-3",
        )
        cls.dismissed.email_draft_subject = "Your 1:1 with Alexey"
        cls.dismissed.email_draft_body = "Great talking!"
        cls.dismissed.email_draft_ready_at = timezone.now()
        cls.dismissed.email_draft_dismissed_at = timezone.now()
        cls.dismissed.save()

        # No draft fields: must not appear in the listing.
        record_call(
            email="attendee-d@test.com",
            occurred_at=timezone.now() - datetime.timedelta(minutes=30),
            summary="Manual note.",
            source="manual",
            source_ref="",
        )

    def _get(self, token=None):
        kwargs = {}
        if token is not None:
            kwargs["HTTP_AUTHORIZATION"] = f"Token {token}"
        return self.client.get(self.URL, **kwargs)

    def test_staff_lists_draft_sent_and_dismissed_rows(self):
        response = self._get(self.staff_token.key)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 3)
        rows = {row["call_id"]: row for row in body["drafts"]}
        self.assertEqual(rows[self.drafted.pk]["status"], "draft")
        self.assertEqual(rows[self.sent.pk]["status"], "sent")
        self.assertEqual(rows[self.dismissed.pk]["status"], "dismissed")
        self.assertEqual(
            rows[self.dismissed.pk]["subject"], "Your 1:1 with Alexey",
        )
        self.assertIsNotNone(rows[self.sent.pk]["sent_at"])
        self.assertIsNotNone(rows[self.dismissed.pk]["dismissed_at"])

    def test_rows_are_most_recent_call_first(self):
        response = self._get(self.staff_token.key)
        body = response.json()
        self.assertEqual(body["drafts"][0]["call_id"], self.dismissed.pk)
        self.assertEqual(body["drafts"][-1]["call_id"], self.drafted.pk)

    def test_member_token_gets_401(self):
        response = self._get(self.member_token.key)
        self.assertEqual(response.status_code, 401)

    def test_missing_token_gets_401(self):
        response = self._get()
        self.assertEqual(response.status_code, 401)

    def test_post_is_not_allowed(self):
        response = self.client.post(
            self.URL,
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(response.status_code, 405)
