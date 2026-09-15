"""Worker resolvers for the slice-2 plans and payments mail (issue #1629).

The five producers persist scalar-only contexts plus the natural relation
(issue #1613). These tests drain real ``EmailDelivery`` rows through the
worker (``email_app.hooks.resolve_auth_mail_context``) and assert the
provider-visible email carries the exact links the old synchronous send
rendered — and that a stale relation fails closed with a
``PermanentJobError`` reason code instead of delivering a broken link.
"""

import datetime
from unittest.mock import patch

from community_base.jobs.runner import PermanentJobError
from community_base.mail.jobs import deliver as deliver_job
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from content.models import Course
from email_app.package_mail import send_package_mail
from email_app.testing import StubSESClient
from payments.models import (
    MonthlyPaymentGrace as Grace,
)
from plans.models import Plan, Sprint, Week
from questionnaires.models import Questionnaire, Response
from tests.fixtures import TierSetupMixin, set_membership

BASE_URL = "https://site.test"


def _drain_html(delivery):
    """Deliver one delivery through the real worker; return (subject, html)."""

    stub = StubSESClient()
    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1
    call = stub.calls[0]
    simple = call["Content"]["Simple"]
    return simple["Subject"]["Data"], simple["Body"]["Html"]["Data"]


def _deliver_expect_permanent_error(test, delivery, reason):
    """Drain one delivery and assert it fails closed with ``reason``."""

    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=StubSESClient(),
    ):
        with test.assertRaises(PermanentJobError) as caught:
            deliver_job(None, {"delivery_id": str(delivery.id)})
    test.assertEqual(caught.exception.code, reason)


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class SprintEndRecapResolverTest(TierSetupMixin, TestCase):
    def _member(self, email, **fields):
        return User.objects.create_user(
            email=email, password="pw", **fields,
        )

    def _plan(self, member):
        sprint = Sprint.objects.create(
            name="May Sprint",
            slug="may-sprint",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
            status="completed",
        )
        return Plan.objects.create(
            sprint=sprint, member=member, shared_at=timezone.now(),
        )

    def _send(self, plan, context):
        return send_package_mail(
            plan.member, "sprint_end_recap", context, related=plan,
        )

    def test_worker_mints_plan_feedback_and_next_action_links(self):
        member = self._member("recap@test.com")
        plan = self._plan(member)
        questionnaire = Questionnaire.objects.create(
            title="Sprint Feedback",
            slug="sprint-feedback",
            purpose="feedback",
        )
        feedback_response = Response.objects.create(
            questionnaire=questionnaire,
            respondent=member,
            status="submitted",
        )
        next_sprint = Sprint.objects.create(
            name="Next Sprint",
            slug="next-sprint",
            start_date=datetime.date(2026, 5, 15),
            duration_weeks=4,
            status="active",
        )
        next_plan = Plan.objects.create(
            sprint=next_sprint, member=member, shared_at=timezone.now(),
        )

        delivery = self._send(plan, {
            "sprint_name": "May Sprint",
            "progress_sentence": "You completed 1 of 2 checkpoints.",
            "has_feedback": True,
            "feedback_cta_label": "View your feedback",
            "feedback_copy": "Your feedback is already submitted.",
            "feedback_response_id": feedback_response.pk,
            "has_next_action": True,
            "next_action_label": "Carry over unfinished work",
            "next_action_copy": "Open your next plan.",
            "next_action_kind": "carry_over",
            "next_action_sprint_id": next_sprint.pk,
            "next_action_plan_id": next_plan.pk,
        })

        # No rendered link ever touches the durable row.
        for url_key in ("plan_url", "feedback_url", "next_action_url"):
            self.assertNotIn(url_key, delivery.context_data)

        subject, html = _drain_html(delivery)
        self.assertEqual(subject, "Your May Sprint sprint recap")
        self.assertIn(f"{BASE_URL}/sprints/may-sprint/plan/{plan.pk}", html)
        self.assertIn(
            f"{BASE_URL}/sprints/may-sprint/feedback/"
            f"{feedback_response.pk}",
            html,
        )
        self.assertIn(
            f"{BASE_URL}/sprints/next-sprint/plan/{next_plan.pk}", html,
        )
        self.assertIn("Carry over unfinished work", html)

    def test_member_without_name_gets_the_1591_greeting(self):
        member = self._member("nameless.recap@test.com")
        plan = self._plan(member)

        delivery = self._send(plan, {
            "sprint_name": "May Sprint",
            "progress_sentence": "You completed 0 of 0 checkpoints.",
            "has_next_action": False,
        })

        _subject, html = _drain_html(delivery)
        self.assertIn("Hi there,", html)
        self.assertNotIn("Hi ,", html)
        self.assertNotIn("Hi nameless.recap,", html)

    def test_deleted_plan_fails_closed_with_reason(self):
        member = self._member("stale-recap@test.com")
        plan = self._plan(member)
        delivery = self._send(plan, {"sprint_name": "May Sprint"})
        plan.delete()

        _deliver_expect_permanent_error(
            self, delivery, "sprint_end_recap_plan_missing",
        )

    def test_deleted_feedback_response_fails_closed_with_reason(self):
        member = self._member("stale-feedback@test.com")
        plan = self._plan(member)
        questionnaire = Questionnaire.objects.create(
            title="Sprint Feedback",
            slug="sprint-feedback-stale",
            purpose="feedback",
        )
        feedback_response = Response.objects.create(
            questionnaire=questionnaire,
            respondent=member,
            status="submitted",
        )
        delivery = self._send(plan, {
            "has_feedback": True,
            "feedback_response_id": feedback_response.pk,
        })
        feedback_response.delete()

        _deliver_expect_permanent_error(
            self, delivery, "sprint_end_recap_feedback_missing",
        )

    def test_deleted_next_action_sprint_fails_closed_with_reason(self):
        member = self._member("stale-action@test.com")
        plan = self._plan(member)
        next_sprint = Sprint.objects.create(
            name="Vanishing Sprint",
            slug="vanishing",
            start_date=datetime.date(2026, 5, 15),
            duration_weeks=4,
            status="active",
        )
        delivery = self._send(plan, {
            "has_next_action": True,
            "next_action_kind": "join_next",
            "next_action_sprint_id": next_sprint.pk,
        })
        next_sprint.delete()

        _deliver_expect_permanent_error(
            self, delivery, "sprint_end_recap_next_action_missing",
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL, SLACK_TEAM_ID="TWORKSPACE")
class SprintPartnerIntroResolverTest(TestCase):
    def _member(self, email, **fields):
        return User.objects.create_user(
            email=email, password="pw", **fields,
        )

    def test_worker_mints_board_and_partner_slack_links(self):
        sprint = Sprint.objects.create(
            name="May Sprint",
            slug="may-sprint",
            start_date=datetime.date(2026, 5, 1),
            status="active",
        )
        member = self._member(
            "intro@test.com",
            first_name="Ada",
            slack_user_id="UMEMBER",
        )
        delivery = send_package_mail(
            member,
            "sprint_partner_intro",
            {
                "sprint_name": "May Sprint",
                "sprint_slug": "may-sprint",
                "member_name": "Ada",
                "partner_count": 1,
                "partners": [{
                    "id": 1,
                    "name": "p.artner",
                    "email": "p.artner@test.com",
                    "slack_user_id": "UPARTNER",
                    "slack_identity": "Partner Slack",
                }],
            },
            related=sprint,
        )
        self.assertNotIn("board_url", delivery.context_data)
        self.assertNotIn(
            "slack_profile_url",
            delivery.context_data["partners"][0],
        )

        subject, html = _drain_html(delivery)
        self.assertIn(
            "Your accountability partner for May Sprint", subject,
        )
        self.assertIn(f"{BASE_URL}/sprints/may-sprint/board", html)
        self.assertIn("https://app.slack.com/client/TWORKSPACE/UPARTNER", html)
        self.assertIn("Ada", html)

    def test_deleted_sprint_fails_closed_with_reason(self):
        sprint = Sprint.objects.create(
            name="Vanished Sprint",
            slug="vanished",
            start_date=datetime.date(2026, 5, 1),
            status="active",
        )
        member = self._member("stale-intro@test.com")
        delivery = send_package_mail(
            member,
            "sprint_partner_intro",
            {"sprint_name": "Vanished Sprint", "partner_count": 0},
            related=sprint,
        )
        sprint.delete()

        _deliver_expect_permanent_error(
            self, delivery, "sprint_partner_intro_sprint_missing",
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class SprintCadenceResolverTest(TestCase):
    def _plan_with_week(self, email, **user_fields):
        member = User.objects.create_user(
            email=email, password="pw", email_verified=True, **user_fields,
        )
        sprint = Sprint.objects.create(
            name="May Sprint",
            slug="may-sprint",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
            status="active",
        )
        plan = Plan.objects.create(
            sprint=sprint, member=member, shared_at=timezone.now(),
        )
        week = Week.objects.create(
            plan=plan, week_number=3, position=2, theme="Ship prototype",
        )
        return plan, week

    def test_worker_mints_plan_url_with_week_anchor(self):
        plan, week = self._plan_with_week("cadence@test.com")
        delivery = send_package_mail(
            plan.member,
            "sprint_week_start",
            {
                "sprint_name": "May Sprint",
                "week_number": 3,
                "week_theme": "Ship prototype",
                "unfinished_count": 2,
                "unfinished_label": "unfinished checkpoints",
                "previous_week_number": "",
                "needs_previous_week_note": False,
                "week_id": week.pk,
            },
            related=plan,
        )
        self.assertNotIn("plan_url", delivery.context_data)

        subject, html = _drain_html(delivery)
        self.assertEqual(subject, "Week 3 is ready for May Sprint")
        self.assertIn(
            f"{BASE_URL}/sprints/may-sprint/plan/{plan.pk}#week-{week.pk}",
            html,
        )

    def test_week_note_prompt_gets_the_same_anchor_rule(self):
        plan, week = self._plan_with_week("prompt@test.com")
        delivery = send_package_mail(
            plan.member,
            "sprint_week_note_prompt",
            {
                "sprint_name": "May Sprint",
                "week_number": 3,
                "week_theme": "Ship prototype",
                "week_id": week.pk,
            },
            related=plan,
        )

        subject, html = _drain_html(delivery)
        self.assertEqual(subject, "Write your Week 3 sprint note")
        self.assertIn(
            f"{BASE_URL}/sprints/may-sprint/plan/{plan.pk}#week-{week.pk}",
            html,
        )

    def test_deleted_plan_fails_closed_with_reason(self):
        plan, week = self._plan_with_week("stale-cadence@test.com")
        delivery = send_package_mail(
            plan.member,
            "sprint_week_start",
            {"week_id": week.pk},
            related=plan,
        )
        plan.delete()

        _deliver_expect_permanent_error(
            self, delivery, "sprint_cadence_plan_missing",
        )

    def test_deleted_week_fails_closed_with_reason(self):
        plan, week = self._plan_with_week("stale-week@test.com")
        delivery = send_package_mail(
            plan.member,
            "sprint_week_start",
            {"week_id": week.pk},
            related=plan,
        )
        week.delete()

        _deliver_expect_permanent_error(
            self, delivery, "sprint_cadence_week_missing",
        )


@tag("core")
@override_settings(
    SITE_BASE_URL=BASE_URL,
    STRIPE_CUSTOMER_PORTAL_URL="https://billing.stripe.com/p/login/safe",
)
class PaymentGraceResolverTest(TierSetupMixin, TestCase):
    def _grace_for(self, member):
        set_membership(member, tier=self.main_tier)
        return Grace.objects.create(
            user=member,
            base_tier_at_start=self.main_tier,
            stripe_customer_id="cus_grace",
            stripe_subscription_id="sub_grace",
            stripe_invoice_id="in_grace",
            livemode=False,
            source=Grace.SOURCE_WEBHOOK,
            interval="month",
            interval_count=1,
            grace_started_at=timezone.now(),
            grace_expires_at=timezone.now() + datetime.timedelta(hours=168),
        )

    def test_worker_mints_portal_studio_links_and_member_email(self):
        member = User.objects.create_user(email="grace@test.com")
        grace = self._grace_for(member)

        delivery = send_package_mail(
            grace.user,
            "payment_grace_failure_team",
            {
                "deadline_utc": "2026-08-19 10:00 UTC",
                "base_tier": "Main",
                "effective_tier": "Main",
                "override_continues": False,
                "stripe_customer_id": "cus_grace",
                "stripe_subscription_id": "sub_grace",
                "stripe_invoice_id": "in_grace",
                "failure_time": "2026-08-12 10:00 UTC",
                "interval": "month x 1",
            },
            recipient_email="team@aishippinglabs.test",
            idempotency_key=f"monthly-payment-grace:{grace.pk}:failure_team:x",
            related=grace,
        )
        for url_key in ("recovery_url", "studio_member_url", "studio_report_url"):
            self.assertNotIn(url_key, delivery.context_data)

        subject, html = _drain_html(delivery)
        self.assertEqual(subject, "[Payments] Member payment failed")
        # The template documents the MEMBER even though the delivery goes
        # to the team mailbox; the package default would have used the
        # delivery recipient here.
        self.assertIn("grace@test.com", html)
        self.assertIn(f"{BASE_URL}/studio/users/{member.pk}/", html)
        self.assertIn(
            f"{BASE_URL}/studio/payments/subscription-reconciliation/"
            "?filter=payment_grace",
            html,
        )

    def test_member_template_renders_the_1591_greeting(self):
        member = User.objects.create_user(email="nameless.grace@test.com")
        grace = self._grace_for(member)

        delivery = send_package_mail(
            grace.user,
            "payment_grace_failure_member",
            {"deadline_utc": "2026-08-19 10:00 UTC"},
            related=grace,
        )

        _subject, html = _drain_html(delivery)
        self.assertIn("Hi there,", html)
        self.assertIn("https://billing.stripe.com/p/login/safe", html)

    def test_deleted_grace_fails_closed_with_reason(self):
        member = User.objects.create_user(email="stale.grace@test.com")
        grace = self._grace_for(member)
        delivery = send_package_mail(
            grace.user,
            "payment_grace_reminder_member",
            {"deadline_utc": "2026-08-19 10:00 UTC"},
            related=grace,
        )
        grace.delete()

        _deliver_expect_permanent_error(
            self, delivery, "payment_grace_record_missing",
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class CheckoutPaymentFailedResolverTest(TestCase):
    def _course(self):
        return Course.objects.create(
            title="Delayed Course",
            slug="delayed-course",
            status="published",
            required_level=20,
        )

    def test_worker_mints_retry_url_from_the_course_relation(self):
        member = User.objects.create_user(email="checkout@test.com")
        course = self._course()
        delivery = send_package_mail(
            member,
            "checkout_payment_failed",
            {"purchase_label": "Delayed Course"},
            idempotency_key="checkout-payment-failed:cs_test_1",
            related=course,
        )
        self.assertNotIn("retry_url", delivery.context_data)

        subject, html = _drain_html(delivery)
        self.assertIn("Your AI Shipping Labs payment didn't complete", subject)
        self.assertIn(
            f"{BASE_URL}{course.get_absolute_url()}", html,
        )

    def test_tier_checkout_without_course_uses_membership_fallback(self):
        member = User.objects.create_user(email="tier.checkout@test.com")
        delivery = send_package_mail(
            member,
            "checkout_payment_failed",
            {"purchase_label": "membership"},
            idempotency_key="checkout-payment-failed:cs_test_2",
        )

        _subject, html = _drain_html(delivery)
        self.assertIn(f"{BASE_URL}/membership", html)

    def test_deleted_course_fails_closed_with_reason(self):
        member = User.objects.create_user(email="stale.checkout@test.com")
        course = self._course()
        delivery = send_package_mail(
            member,
            "checkout_payment_failed",
            {"purchase_label": "Delayed Course"},
            idempotency_key="checkout-payment-failed:cs_test_3",
            related=course,
        )
        course.delete()

        _deliver_expect_permanent_error(
            self, delivery, "checkout_payment_failed_course_missing",
        )
