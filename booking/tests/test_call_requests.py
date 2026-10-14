from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from booking.models import (
    STATUS_CONFIRMED,
    STATUS_PENDING,
    Booking,
    BookingHost,
    CallRequest,
)
from questionnaires.models import Questionnaire, Response


class CallRequestWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.com",
            password="password123",
            first_name="Member",
            last_name="One",
        )
        self.questionnaire, _ = Questionnaire.objects.get_or_create(
            slug="generic-onboarding",
            defaults={"title": "Generic Onboarding", "purpose": "onboarding"},
        )
        Response.objects.create(
            questionnaire=self.questionnaire,
            respondent=self.user,
            status="submitted",
        )
        self.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "email": "alexey@datatalks.club",
                "timezone": "Europe/Berlin",
                "is_active": True,
            },
        )

    def test_request_form_rendered_only_when_allowance_zero(self):
        self.client.force_login(self.user)

        # Allowance is 1: /book/alexey renders booking container, NOT request-call-form
        resp_book = self.client.get("/book/alexey")
        self.assertContains(resp_book, 'data-testid="booking-container"')
        self.assertNotContains(resp_book, 'data-testid="request-call-form"')

        # Consume allowance with a past confirmed booking
        now = timezone.now()
        Booking.objects.create(
            host=self.host,
            member=self.user,
            invitee_name="Member One",
            invitee_email=self.user.email,
            start_datetime=now - timedelta(days=2),
            end_datetime=now - timedelta(days=2, minutes=-30),
            status=STATUS_CONFIRMED,
        )

        # Allowance is now 0: both /book/alexey and /request-a-call render request-call-form
        resp_book_zero = self.client.get("/book/alexey")
        self.assertContains(resp_book_zero, 'data-testid="request-call-form"')
        self.assertNotContains(resp_book_zero, 'data-testid="booking-container"')

        resp_request_call = self.client.get("/request-a-call")
        self.assertContains(resp_request_call, 'data-testid="request-call-form"')

    def test_request_submission_creates_pending_call_request(self):
        self.client.force_login(self.user)
        mail.outbox = []

        resp = self.client.post(
            "/request-a-call",
            {
                "topic": "AI Project Architecture & Feedback",
                "message": "I would like to discuss our production LLM deployment architecture.",
            },
        )
        self.assertRedirects(resp, "/request-a-call")

        call_req = CallRequest.objects.filter(member=self.user).first()
        self.assertIsNotNone(call_req)
        self.assertEqual(call_req.status, STATUS_PENDING)
        self.assertEqual(call_req.topic, "AI Project Architecture & Feedback")
        self.assertIn("production LLM deployment", call_req.message)

    def test_duplicate_pending_request_prevented(self):
        self.client.force_login(self.user)
        CallRequest.objects.create(
            member=self.user,
            host=self.host,
            topic="Career Strategy",
            message="Initial request message",
            status=STATUS_PENDING,
        )

        # Visiting pages should display pending request banner
        resp_get = self.client.get("/request-a-call")
        self.assertContains(resp_get, 'data-testid="pending-call-request-banner"')
        self.assertNotContains(resp_get, 'data-testid="request-call-form"')

        resp_book = self.client.get("/book/alexey")
        self.assertContains(resp_book, 'data-testid="pending-call-request-banner"')

        # Attempting to post a second request is rejected
        resp_post = self.client.post(
            "/request-a-call",
            {
                "topic": "Another topic",
                "message": "Duplicate request",
            },
        )
        self.assertRedirects(resp_post, "/request-a-call")
        self.assertEqual(CallRequest.objects.filter(member=self.user).count(), 1)

    def test_request_submission_emails_alexey_only(self):
        self.client.force_login(self.user)
        mail.outbox = []

        resp = self.client.post(
            "/request-a-call",
            {
                "topic": "Sprint & Career Strategy",
                "message": "Need advice on transitioning to AI engineering.",
            },
        )
        self.assertRedirects(resp, "/request-a-call")

        # Must send exactly 1 email to Alexey (host.email)
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertEqual(sent_email.to, ["alexey@datatalks.club"])
        self.assertIn("Member One", sent_email.subject)
        self.assertIn("Sprint & Career Strategy", sent_email.body)
        self.assertIn("transitioning to AI engineering", sent_email.body)
        self.assertIn("/studio/call-requests/", sent_email.body)
        self.assertIn(f"/studio/crm/{self.user.pk}/", sent_email.body)

    def test_request_submission_sends_no_email_to_member(self):
        self.client.force_login(self.user)
        mail.outbox = []

        self.client.post(
            "/request-a-call",
            {
                "topic": "Course / Workshop Question",
                "message": "Question about course assignments.",
            },
        )

        # None of the recipients should be the member
        for sent_email in mail.outbox:
            self.assertNotIn(self.user.email, sent_email.to)
            self.assertNotIn(self.user.email, sent_email.cc)
            self.assertNotIn(self.user.email, sent_email.bcc)
