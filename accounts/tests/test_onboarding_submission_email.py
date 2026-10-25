"""Unit tests for onboarding completion member transactional email (#2006, AC3)."""

from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.tests.test_onboarding_booking_ctas import _basic_tier
from booking.models import BookingHost
from community.models import CallHost
from email_app.testing import StubRelayClient
from questionnaires.models import Response
from tests.fixtures import set_membership

User = get_user_model()


class OnboardingSubmissionEmailTest(TestCase):
    """AC3: Member receives transactional email on first submit only, never broken by send failure."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="onboarding_member@test.com",
            password="pw",
            first_name="Ada",
        )
        set_membership(cls.member, tier=_basic_tier())

    def setUp(self):
        BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={"is_active": True, "name": "Alexey Grigorev"},
        )
        self.client.force_login(self.member)

    def _start_draft_response(self):
        self.client.post(reverse("onboarding_identify"), {"self_id": "none"})
        return Response.objects.get(respondent=self.member)

    def _submit_form(self, response, answer_text="Building an agent"):
        first_text = response.response_questions.filter(
            question_type__in=("text", "long_text"),
        ).first()
        post = {f"question_{first_text.pk}": answer_text}
        for rq in response.response_questions.filter(is_required=True):
            field = f"question_{rq.pk}"
            if rq.question_type in ("text", "long_text"):
                post[field] = answer_text
            elif rq.question_type in ("scale", "number"):
                post[field] = "3"
            else:
                post[field] = str(rq.options.first().pk)
        return self.client.post(
            reverse("onboarding_submit", kwargs={"response_id": response.pk}),
            post,
            follow=False,
        )

    def test_first_submit_sends_exactly_one_delivery(self):
        response = self._start_draft_response()
        resp = self._submit_form(response, "Initial answers")
        self.assertEqual(resp.status_code, 302)

        deliveries = EmailDelivery.objects.filter(
            purpose="onboarding_submitted",
            recipient_user=self.member,
        )
        self.assertEqual(deliveries.count(), 1)
        delivery = deliveries.first()
        self.assertEqual(
            delivery.idempotency_key, f"onboarding_submitted:{response.id}"
        )
        self.assertEqual(delivery.context_data, {})
        self.assertEqual(delivery.related_object_type, "questionnaires.response")
        self.assertEqual(delivery.related_object_id, str(response.id))

        stub = StubRelayClient()
        with patch(
            "community_base.mail.backends.relay.configured_client",
            return_value=stub,
        ):
            deliver_job(None, {"delivery_id": str(delivery.id)})

        self.assertEqual(len(stub.calls), 1)
        call = stub.calls[0]
        self.assertEqual(call["delivery"].purpose, "onboarding_submitted")
        self.assertEqual(call["context"]["user_name"], "Ada")
        self.assertEqual(
            call["context"]["booking_url"].rstrip("/"),
            "https://aishippinglabs.com/book/alexey",
        )

    def test_edit_submission_sends_no_additional_delivery(self):
        response = self._start_draft_response()
        self._submit_form(response, "Initial answers")
        self.assertEqual(
            EmailDelivery.objects.filter(
                purpose="onboarding_submitted",
                recipient_user=self.member,
            ).count(),
            1,
        )

        response.refresh_from_db()
        self.assertEqual(response.status, "submitted")

        # Edit answers and save
        self._submit_form(response, "Updated answers")
        self.assertEqual(
            EmailDelivery.objects.filter(
                purpose="onboarding_submitted",
                recipient_user=self.member,
            ).count(),
            1,
        )

    def test_double_submit_sends_no_additional_delivery(self):
        response = self._start_draft_response()
        self._submit_form(response, "Identical answers")
        self.assertEqual(
            EmailDelivery.objects.filter(
                purpose="onboarding_submitted",
                recipient_user=self.member,
            ).count(),
            1,
        )

        response.refresh_from_db()
        # Resubmit with identical answers (double-click)
        resp = self._submit_form(response, "Identical answers")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            EmailDelivery.objects.filter(
                purpose="onboarding_submitted",
                recipient_user=self.member,
            ).count(),
            1,
        )

    def test_send_failure_does_not_break_submission(self):
        response = self._start_draft_response()
        with patch(
            "accounts.services.onboarding_mail.send_package_mail",
            side_effect=RuntimeError("SES network timeout"),
        ):
            resp = self._submit_form(response, "Answers despite email failure")

        self.assertEqual(resp.status_code, 302)
        response.refresh_from_db()
        self.assertEqual(response.status, "submitted")

    def test_email_uses_calendly_fallback_when_no_native_host(self):
        BookingHost.objects.filter(slug="alexey").update(is_active=False)
        CallHost.objects.filter(slug="alexey").update(
            is_active=True,
            booking_url="https://calendly.com/dtc-alexey/ai-shipping-labs-call",
        )
        response = self._start_draft_response()
        self._submit_form(response, "Fallback test")

        delivery = EmailDelivery.objects.filter(
            purpose="onboarding_submitted",
            recipient_user=self.member,
        ).first()
        stub = StubRelayClient()
        with patch(
            "community_base.mail.backends.relay.configured_client",
            return_value=stub,
        ):
            deliver_job(None, {"delivery_id": str(delivery.id)})

        self.assertEqual(
            stub.calls[0]["context"]["booking_url"],
            "https://calendly.com/dtc-alexey/ai-shipping-labs-call",
        )
