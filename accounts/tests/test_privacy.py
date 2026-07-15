import json
from datetime import date, timedelta
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.sessions.models import Session
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import (
    SIGNUP_SOURCE_NEWSLETTER,
    EmailAlias,
    MemberAPIKey,
    PrivacyRequestLog,
    User,
)
from accounts.services.privacy import (
    REDACTED,
    build_user_data_export,
    delete_account_for_privacy,
)
from analytics.models import UserActivity
from comments.models import Comment
from content.models import Course, Enrollment, Project, UserContentCompletion
from crm.models import CRMRecord, SlackMessage, SlackThread
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from notifications.models import Notification
from payments.models import (
    ConversionAttribution,
    PaymentAccountMismatch,
    WebhookEvent,
)
from plans.models import Plan, Sprint
from tests.fixtures import TierSetupMixin


def _course(slug="privacy-course"):
    return Course.objects.create(
        slug=slug,
        title="Privacy Course",
        description="Course",
        status="published",
    )


def _event(slug="privacy-event"):
    return Event.objects.create(
        slug=slug,
        title="Privacy Event",
        description="Event",
        start_datetime=timezone.now() + timedelta(days=3),
        status="upcoming",
    )


def _sprint(slug="privacy-sprint"):
    return Sprint.objects.create(
        slug=slug,
        name="Privacy Sprint",
        start_date=date(2026, 7, 1),
        duration_weeks=4,
        status="active",
        min_tier_level=0,
    )


@tag("core")
class PrivacyAccountViewTest(TestCase):
    def test_privacy_section_renders_for_member_and_newsletter_only_user(self):
        member = User.objects.create_user(email="member-privacy@test.com")
        self.client.force_login(member)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="privacy-data-section"')
        self.assertContains(response, 'data-testid="privacy-export-link"')
        self.assertContains(response, 'data-testid="privacy-delete-form"')

        newsletter = User.objects.create_user(
            email="newsletter-privacy@test.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )
        self.client.force_login(newsletter)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="newsletter-only-cta"')
        self.assertContains(response, 'data-testid="privacy-data-section"')
        self.assertContains(response, 'data-testid="privacy-export-link"')

    def test_anonymous_export_and_delete_use_login_redirect(self):
        export_response = self.client.get("/account/api/data-export")
        delete_response = self.client.post("/account/api/delete-account")

        self.assertEqual(export_response.status_code, 302)
        self.assertIn("/accounts/login/", export_response.url)
        self.assertEqual(delete_response.status_code, 302)
        self.assertIn("/accounts/login/", delete_response.url)


@tag("core")
class PrivacyExportTest(TierSetupMixin, TestCase):
    def test_data_export_returns_attachment_json_and_audits(self):
        user = User.objects.create_user(
            email="export@test.com",
            password="TestPass123!",
            first_name="Export",
            email_verified=True,
        )
        user.email_preferences = {"newsletter": True}
        user.dashboard_dismissals = ["slack_join"]
        user.tier = self.main_tier
        user.stripe_customer_id = "cus_export"
        user.slack_user_id = "U_EXPORT"
        user.save(
            update_fields=[
                "email_preferences",
                "dashboard_dismissals",
                "tier",
                "stripe_customer_id",
                "slack_user_id",
            ]
        )
        EmailAlias.objects.create(user=user, email="alias-export@test.com")
        member_key, plaintext = MemberAPIKey.create_for_user(
            user=user,
            name="local codex",
        )
        course = _course()
        Enrollment.objects.create(user=user, course=course)
        UserContentCompletion.objects.create(
            user=user,
            content_type="workshop_page",
            object_id=123,
            completed_at=timezone.now(),
        )
        event = _event()
        EventRegistration.objects.create(event=event, user=user)
        sprint = _sprint()
        plan = Plan.objects.create(member=user, sprint=sprint, goal="Ship GDPR")
        SlackThread.objects.create(
            channel_id="C123",
            thread_ts="111.222",
            member=user,
            plan=plan,
            posted_at=timezone.now(),
        )
        other_thread = SlackThread.objects.create(
            channel_id="C123",
            thread_ts="111.333",
            slack_user_id="U_OTHER",
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=other_thread,
            ts="111.334",
            slack_user_id="U_EXPORT",
            text="reply authored in another thread",
            posted_at=timezone.now(),
        )
        EmailLog.objects.create(user=user, email_type="welcome")
        UserActivity.objects.create(
            user=user,
            event_type=UserActivity.EVENT_EVENT_REGISTER,
            occurred_at=timezone.now(),
            label="Registered",
        )

        self.client.force_login(user)
        response = self.client.get("/account/api/data-export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn(
            'attachment; filename="ai-shipping-labs-data-',
            response["Content-Disposition"],
        )
        payload = json.loads(response.content)
        self.assertEqual(payload["manifest"]["primary_email"], "export@test.com")
        self.assertEqual(
            payload["events_community"]["slack_authored_messages"][0]["text"],
            "reply authored in another thread",
        )
        self.assertEqual(payload["membership_payment"]["current_tier"]["slug"], "main")
        self.assertEqual(
            payload["auth_security"]["member_api_keys"][0]["lookup_prefix"],
            member_key.lookup_prefix,
        )
        self.assertEqual(
            payload["learning_content"]["course_enrollments"][0]["course_id"],
            course.pk,
        )
        self.assertEqual(
            payload["events_community"]["event_registrations"][0]["event_id"],
            event.pk,
        )
        self.assertEqual(payload["sprints_plans"]["plans"][0]["goal"], "Ship GDPR")
        self.assertEqual(
            payload["communications_activity"]["email_logs"][0]["email_type"],
            "welcome",
        )

        body = response.content.decode()
        self.assertNotIn(plaintext, body)
        self.assertNotIn(member_key.key_hash, body)
        self.assertNotIn("password", payload["auth_security"]["member_api_keys"][0])
        self.assertNotIn("card_number", body)

        log = PrivacyRequestLog.objects.get(request_type="export")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_COMPLETED)
        self.assertEqual(log.old_user_id, user.pk)
        self.assertEqual(log.email_domain, "test.com")
        self.assertNotIn("export@test.com", json.dumps(log.row_count_summary))

    def test_newsletter_only_export_has_empty_member_categories(self):
        user = User.objects.create_user(
            email="newsletter-export@test.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )
        payload = build_user_data_export(user)

        self.assertEqual(
            payload["manifest"]["primary_email"],
            "newsletter-export@test.com",
        )
        self.assertEqual(payload["learning_content"]["course_enrollments"], [])
        self.assertEqual(payload["events_community"]["event_registrations"], [])
        self.assertEqual(payload["sprints_plans"]["plans"], [])

    def test_oauth_social_account_export_redacts_provider_secrets(self):
        user = User.objects.create_user(email="oauth-export@test.com")
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-stable-uid",
            extra_data={
                "email": "oauth-export@test.com",
                "name": "OAuth Export",
                "locale": "en",
                "picture": "https://example.com/avatar.png?sz=96",
                "access_token": "ya29.raw-access-token",
                "refresh_token": "raw-refresh-token",
                "nested": {
                    "client_secret": "raw-client-secret",
                    "authorization": "Bearer raw-authorization-token",
                    "public_profile": "builder",
                },
                "token_list": [
                    "gho_raw-github-token",
                    {
                        "id_token": ("aaaaaaaaaabbbbbbbbbb.ccccccccccdddddddddd.eeeeeeeeeeffffffffff"),
                    },
                ],
                "provider_values": [
                    "ghp_raw-provider-token",
                    {
                        "jwt_claim": ("1111111111aaaaaaaaaa.2222222222bbbbbbbbbb.3333333333cccccccccc"),
                    },
                ],
                "avatar_url": ("https://example.com/photo.png?size=96&access_token=raw-query-token"),
            },
        )

        payload = build_user_data_export(user)

        account = payload["auth_security"]["oauth_social_accounts"][0]
        metadata = account["extra_data"]
        self.assertEqual(account["provider"], "google")
        self.assertEqual(account["uid"], "google-stable-uid")
        self.assertEqual(metadata["email"], "oauth-export@test.com")
        self.assertEqual(metadata["name"], "OAuth Export")
        self.assertEqual(metadata["locale"], "en")
        self.assertEqual(metadata["picture"], "https://example.com/avatar.png?sz=96")
        self.assertEqual(metadata["nested"]["public_profile"], "builder")

        self.assertEqual(metadata["access_token"], REDACTED)
        self.assertEqual(metadata["refresh_token"], REDACTED)
        self.assertEqual(metadata["nested"]["client_secret"], REDACTED)
        self.assertEqual(metadata["nested"]["authorization"], REDACTED)
        self.assertEqual(metadata["token_list"], REDACTED)
        self.assertEqual(metadata["provider_values"][0], REDACTED)
        self.assertEqual(metadata["provider_values"][1]["jwt_claim"], REDACTED)

        body = json.dumps(payload)
        self.assertNotIn("raw-access-token", body)
        self.assertNotIn("raw-refresh-token", body)
        self.assertNotIn("raw-client-secret", body)
        self.assertNotIn("raw-authorization-token", body)
        self.assertNotIn("raw-github-token", body)
        self.assertNotIn("raw-provider-token", body)
        self.assertNotIn("raw-query-token", body)


@tag("core")
class PrivacyDeletionGuardTest(TierSetupMixin, TestCase):
    def test_bad_email_and_password_are_blocked_and_audited(self):
        user = User.objects.create_user(
            email="guard@test.com",
            password="TestPass123!",
        )
        self.client.force_login(user)

        email_response = self.client.post(
            "/account/api/delete-account",
            {"confirm_email": "typo@test.com", "current_password": "TestPass123!"},
        )
        password_response = self.client.post(
            "/account/api/delete-account",
            {"confirm_email": "guard@test.com", "current_password": "wrong"},
        )

        self.assertEqual(email_response.status_code, 400)
        self.assertEqual(password_response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertEqual(
            list(
                PrivacyRequestLog.objects.filter(
                    request_type="delete",
                    status=PrivacyRequestLog.STATUS_BLOCKED,
                )
                .order_by("requested_at")
                .values_list("blocker_reason", flat=True)
            ),
            [
                PrivacyRequestLog.BLOCKER_BAD_CONFIRMATION,
                PrivacyRequestLog.BLOCKER_BAD_PASSWORD,
            ],
        )

    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_active_subscription_blocks_deletion_and_notifies_staff(self, notify):
        user = User.objects.create_user(
            email="paid-delete@test.com",
            password="TestPass123!",
        )
        user.tier = self.basic_tier
        user.subscription_id = "sub_active"
        user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(user)

        response = self.client.post(
            "/account/api/delete-account",
            {
                "confirm_email": "paid-delete@test.com",
                "current_password": "TestPass123!",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertContains(response, "active subscription", status_code=403)
        log = PrivacyRequestLog.objects.get(request_type="delete")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_BLOCKED)
        self.assertEqual(
            log.blocker_reason,
            PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION,
        )
        notify.assert_called_once()

    def test_staff_account_is_blocked_from_self_service_delete(self):
        staff = User.objects.create_user(
            email="staff-delete@test.com",
            password="TestPass123!",
            is_staff=True,
        )
        self.client.force_login(staff)

        page = self.client.get("/account/")
        response = self.client.post(
            "/account/api/delete-account",
            {
                "confirm_email": "staff-delete@test.com",
                "current_password": "TestPass123!",
            },
        )

        self.assertContains(page, 'data-testid="privacy-staff-block"')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=staff.pk).exists())
        log = PrivacyRequestLog.objects.get(request_type="delete")
        self.assertEqual(log.blocker_reason, PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT)


@tag("core")
class PrivacyDeletionSuccessTest(TierSetupMixin, TestCase):
    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_successful_deletion_erases_member_rows_and_invalidates_session(
        self,
        notify,
    ):
        user = User.objects.create_user(
            email="delete-success@test.com",
            password="TestPass123!",
            first_name="Delete",
        )
        user.slack_user_id = "U_DELETE"
        user.save(update_fields=["slack_user_id"])
        course = _course("delete-course")
        Enrollment.objects.create(user=user, course=course)
        event = _event("delete-event")
        EventRegistration.objects.create(event=event, user=user)
        sprint = _sprint("delete-sprint")
        plan = Plan.objects.create(member=user, sprint=sprint, goal="Erase me")
        MemberAPIKey.create_for_user(user=user, name="delete key")
        Notification.objects.create(user=user, title="Delete", body="Soon")
        Comment.objects.create(user=user, content_id=plan.comment_content_id, body="Hi")
        CRMRecord.objects.create(user=user, summary="Private CRM")
        thread = SlackThread.objects.create(
            channel_id="CDEL",
            thread_ts="222.333",
            member=user,
            plan=plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread,
            ts="222.333",
            text="private sprint update",
            posted_at=timezone.now(),
            is_root=True,
        )
        retained_thread = SlackThread.objects.create(
            channel_id="CDEL",
            thread_ts="333.444",
            slack_user_id="U_OTHER",
            posted_at=timezone.now(),
            reply_count=1,
        )
        SlackMessage.objects.create(
            thread=retained_thread,
            ts="333.444",
            slack_user_id="U_OTHER",
            text="other member root",
            posted_at=timezone.now(),
            is_root=True,
        )
        SlackMessage.objects.create(
            thread=retained_thread,
            ts="333.445",
            slack_user_id="U_DELETE",
            text="delete my reply",
            posted_at=timezone.now(),
        )
        Project.objects.create(
            title="Published member project",
            slug="published-member-project",
            description="Public",
            date=date(2026, 7, 1),
            author="Delete",
            submitter=user,
            published=True,
            status="published",
        )
        Project.objects.create(
            title="Draft member project",
            slug="draft-member-project",
            description="Private",
            date=date(2026, 7, 2),
            author="Delete",
            submitter=user,
            published=False,
            status="pending_review",
        )

        self.client.force_login(user)
        session_key = self.client.session.session_key
        response = self.client.post(
            "/account/api/delete-account",
            {
                "confirm_email": "delete-success@test.com",
                "current_password": "TestPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/account/deleted")
        self.assertFalse(User.objects.filter(email="delete-success@test.com").exists())
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertFalse(
            self.client.login(
                email="delete-success@test.com",
                password="TestPass123!",
            )
        )
        self.assertFalse(Enrollment.objects.filter(course=course).exists())
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assertFalse(Plan.objects.filter(pk=plan.pk).exists())
        self.assertFalse(MemberAPIKey.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(CRMRecord.objects.exists())
        self.assertEqual(SlackThread.objects.count(), 2)
        erased_thread = SlackThread.objects.get(thread_ts="222.333")
        self.assertTrue(erased_thread.privacy_erased)
        self.assertIsNone(erased_thread.member_id)
        self.assertIsNone(erased_thread.plan_id)
        self.assertEqual(erased_thread.slack_user_id, "")
        self.assertEqual(erased_thread.messages.get().text, "")
        retained_thread.refresh_from_db()
        self.assertEqual(retained_thread.reply_count, 1)
        self.assertEqual(retained_thread.messages.count(), 2)
        self.assertFalse(
            SlackMessage.objects.filter(slack_user_id="U_DELETE").exists()
        )
        erased_reply = retained_thread.messages.get(ts="333.445")
        self.assertEqual(erased_reply.text, "")
        self.assertEqual(erased_reply.author_display, "")
        self.assertTrue(Project.objects.filter(slug="published-member-project").exists())
        published = Project.objects.get(slug="published-member-project")
        self.assertIsNone(published.submitter)
        self.assertEqual(published.author, "Deleted member")
        self.assertFalse(Project.objects.filter(slug="draft-member-project").exists())

        log = PrivacyRequestLog.objects.get(request_type="delete")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_COMPLETED)
        self.assertEqual(log.old_user_id, user.pk)
        self.assertIn("accounts.User", log.row_count_summary["erased"])
        self.assertIn(
            "published_submitted_projects",
            log.row_count_summary["anonymized"],
        )
        notify.assert_called_once()

    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_retains_payment_records_and_scrubs_webhook_payload(self, notify):
        user = User.objects.create_user(email="stripe-delete@test.com")
        old_user_id = user.pk
        user.stripe_customer_id = "cus_delete"
        user.subscription_id = ""
        user.save(update_fields=["stripe_customer_id", "subscription_id"])
        ConversionAttribution.objects.create(
            user=user,
            stripe_session_id="cs_delete",
            stripe_subscription_id="sub_old",
            tier=self.basic_tier,
            billing_period="monthly",
            amount_eur=20,
            mrr_eur=20,
        )
        PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_mismatch",
            stripe_customer_id="cus_delete",
            stripe_subscription_id="sub_old",
            stripe_email="stripe-delete@test.com",
            paid_user=user,
            reason=PaymentAccountMismatch.REASON_UNKNOWN_REFERENCE,
            details={"email": "stripe-delete@test.com", "customer": "cus_delete"},
        )
        WebhookEvent.objects.create(
            stripe_event_id="evt_delete",
            event_type="checkout.session.completed",
            payload={
                "data": {
                    "object": {
                        "customer": "cus_delete",
                        "customer_email": "stripe-delete@test.com",
                    },
                },
            },
        )

        result = delete_account_for_privacy(user, {"ip": "127.0.0.1"})

        self.assertTrue(result.success)
        attribution = ConversionAttribution.objects.get(stripe_session_id="cs_delete")
        self.assertIsNone(attribution.user)
        mismatch = PaymentAccountMismatch.objects.get(stripe_session_id="cs_mismatch")
        self.assertIsNone(mismatch.paid_user)
        self.assertEqual(
            mismatch.stripe_email,
            f"deleted-user-{old_user_id}@privacy.invalid",
        )
        self.assertEqual(mismatch.details["email"], "[privacy-redacted]")
        event = WebhookEvent.objects.get(stripe_event_id="evt_delete")
        payload_text = json.dumps(event.payload)
        self.assertNotIn("stripe-delete@test.com", payload_text)
        self.assertNotIn("cus_delete", payload_text)
        self.assertIn("scrubbed_webhook_events", result.row_count_summary["retained"])
        log = PrivacyRequestLog.objects.get(request_type="delete")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_COMPLETED)
        self.assertTrue(
            PrivacyRequestLog.objects.filter(pk=log.pk).exists(),
            "PrivacyRequestLog must survive User deletion.",
        )
        notify.assert_called_once()


@tag("core")
class PrivacyRequestLogAdminTest(TestCase):
    def test_staff_can_view_minimal_privacy_request_trail_in_admin(self):
        staff = User.objects.create_superuser(
            email="privacy-admin@test.com",
            password="TestPass123!",
        )
        log = PrivacyRequestLog.objects.create(
            request_type=PrivacyRequestLog.REQUEST_EXPORT,
            status=PrivacyRequestLog.STATUS_COMPLETED,
            old_user_id=12345,
            normalized_email_hash="hash-only",
            email_domain="example.com",
            row_count_summary={"erased": {"sessions": 1}},
            request_ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        )

        self.client.force_login(staff)

        changelist = self.client.get("/admin/accounts/privacyrequestlog/")
        self.assertEqual(changelist.status_code, 200)
        self.assertContains(changelist, "example.com")
        self.assertContains(changelist, "export")
        self.assertNotContains(changelist, "primary_email")

        detail = self.client.get(f"/admin/accounts/privacyrequestlog/{log.pk}/change/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "hash-only")
        self.assertContains(detail, "ip-hash")
        self.assertNotContains(detail, "primary_email")
