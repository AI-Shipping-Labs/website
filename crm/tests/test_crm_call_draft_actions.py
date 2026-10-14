"""Studio mark-as-sent / dismiss actions on the attendee email draft.

Issue #1951: the scheduled pipeline never sends email; these staff
buttons are the only send/mark paths, with double-POST and dismissed-
draft guards.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crm.services.calls import record_call

User = get_user_model()


class DraftActionBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="actions-staff@test.com", password="pw", is_staff=True,
        )
        cls.nonstaff = User.objects.create_user(
            email="actions-member@test.com", password="pw",
        )

    def _make_call(self, **extra):
        call = record_call(
            email="actions-attendee@test.com",
            occurred_at=timezone.now() - datetime.timedelta(hours=2),
            summary="Booked: portfolio review.",
            source="booking",
            source_ref="BK-ACTION-1",
        )
        for field, value in extra.items():
            setattr(call, field, value)
        call.save()
        return call

    def _call_with_draft(self):
        return self._make_call(
            email_draft_subject="Your 1:1 with Alexey",
            email_draft_body="Great talking!",
            email_draft_ready_at=timezone.now(),
        )

    def _detail(self, call):
        return self.client.get(
            reverse("studio_crm_call_detail", args=[call.pk]),
        )

    def _post(self, url_name, call):
        return self.client.post(
            reverse(url_name, args=[call.pk]),
        )


class DraftSectionRenderTest(DraftActionBase):
    def test_ready_draft_shows_all_three_actions(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        response = self._detail(call)
        self.assertContains(response, 'data-testid="crm-call-email-send"')
        self.assertContains(response, 'data-testid="crm-call-email-mark-sent"')
        self.assertContains(response, 'data-testid="crm-call-email-dismiss"')

    def test_booking_call_without_capture_state_shows_empty_state(self):
        self.client.force_login(self.staff)
        call = self._make_call()
        response = self._detail(call)
        self.assertContains(response, 'data-testid="crm-call-email-draft-empty"')
        self.assertNotContains(response, 'data-testid="crm-call-email-send"')

    def test_dismissed_draft_hides_actions_and_shows_marker(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        call.email_draft_dismissed_at = timezone.now()
        call.save(update_fields=["email_draft_dismissed_at"])
        response = self._detail(call)
        self.assertNotContains(response, 'data-testid="crm-call-email-send"')
        self.assertNotContains(
            response, 'data-testid="crm-call-email-mark-sent"',
        )
        self.assertContains(response, 'data-testid="crm-call-email-dismissed"')


class MarkSentActionTest(DraftActionBase):
    def test_mark_sent_stamps_sent_at_and_sent_by(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        response = self._post("studio_crm_call_mark_sent", call)
        self.assertEqual(response.status_code, 302)
        call.refresh_from_db()
        self.assertIsNotNone(call.email_sent_at)
        self.assertEqual(call.email_sent_by, self.staff)
        page = self._detail(call)
        self.assertContains(page, 'data-testid="crm-call-email-sent"')

    def test_double_mark_sent_is_idempotent(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        self._post("studio_crm_call_mark_sent", call)
        call.refresh_from_db()
        first_sent_at = call.email_sent_at
        response = self._post("studio_crm_call_mark_sent", call)
        self.assertIn(response.status_code, (200, 302))
        call.refresh_from_db()
        self.assertEqual(call.email_sent_at, first_sent_at)

    def test_mark_sent_after_dismiss_is_rejected(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        call.email_draft_dismissed_at = timezone.now()
        call.save(update_fields=["email_draft_dismissed_at"])
        response = self._post("studio_crm_call_mark_sent", call)
        self.assertIn(response.status_code, (200, 302))
        call.refresh_from_db()
        self.assertIsNone(call.email_sent_at)

    def test_mark_sent_without_draft_does_nothing(self):
        self.client.force_login(self.staff)
        call = self._make_call()
        response = self._post("studio_crm_call_mark_sent", call)
        self.assertIn(response.status_code, (200, 302))
        call.refresh_from_db()
        self.assertIsNone(call.email_sent_at)

    def test_non_staff_cannot_mark_sent(self):
        self.client.force_login(self.nonstaff)
        call = self._call_with_draft()
        response = self._post("studio_crm_call_mark_sent", call)
        self.assertEqual(response.status_code, 403)
        call.refresh_from_db()
        self.assertIsNone(call.email_sent_at)


class DismissDraftActionTest(DraftActionBase):
    def test_dismiss_stamps_dismissed_at(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        response = self._post("studio_crm_call_dismiss_draft", call)
        self.assertEqual(response.status_code, 302)
        call.refresh_from_db()
        self.assertIsNotNone(call.email_draft_dismissed_at)
        self.assertIsNone(call.email_sent_at)

    def test_double_dismiss_is_idempotent(self):
        self.client.force_login(self.staff)
        call = self._call_with_draft()
        self._post("studio_crm_call_dismiss_draft", call)
        call.refresh_from_db()
        first = call.email_draft_dismissed_at
        response = self._post("studio_crm_call_dismiss_draft", call)
        self.assertIn(response.status_code, (200, 302))
        call.refresh_from_db()
        self.assertEqual(call.email_draft_dismissed_at, first)

    def test_non_staff_cannot_dismiss(self):
        self.client.force_login(self.nonstaff)
        call = self._call_with_draft()
        response = self._post("studio_crm_call_dismiss_draft", call)
        self.assertEqual(response.status_code, 403)
        call.refresh_from_db()
        self.assertIsNone(call.email_draft_dismissed_at)
