import json
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import Client, TestCase, tag
from django.utils import timezone

from accounts.models import SIGNUP_SOURCE_NEWSLETTER, PrivacyRequestLog, User
from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)
from email_app.services.email_service import (
    EMAIL_TYPES_WITHOUT_VERIFY_FOOTER,
    EmailServiceError,
)
from tests.fixtures import TierSetupMixin


@tag("core")
class AccountDeletionRequestViewTest(TierSetupMixin, TestCase):
    def _user(self, email="requester@example.com", **kwargs):
        return User.objects.create_user(
            email=email,
            password="TestPass123!",
            tier=kwargs.pop("tier", self.free_tier),
            **kwargs,
        )

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-privacy-1398",
    )
    def test_request_sends_one_transactional_team_message_with_visible_cc(
        self,
        send_ses,
    ):
        user = self._user(
            email="canonical@example.com",
            email_verified=False,
            unsubscribed=True,
        )
        self.client.force_login(user)

        response = self.client.post(
            "/account/api/request-deletion",
            {"email": "attacker@example.com", "user_id": 999999},
            REMOTE_ADDR="192.0.2.10",
            HTTP_USER_AGENT="privacy-test-agent",
        )

        self.assertRedirects(
            response,
            "/account/#privacy-data-section",
            fetch_redirect_response=False,
        )
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(self.client.get("/account/").wsgi_request.user.is_authenticated)

        audit = PrivacyRequestLog.objects.get(
            request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
        )
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_REQUESTED)
        self.assertEqual(audit.old_user_id, user.pk)
        self.assertEqual(audit.email_domain, "example.com")
        self.assertTrue(audit.normalized_email_hash)
        self.assertTrue(audit.request_ip_hash)
        self.assertTrue(audit.user_agent_hash)
        audit_payload = json.dumps(audit.row_count_summary)
        self.assertNotIn(user.email, audit_payload)
        self.assertNotIn("attacker@example.com", audit_payload)

        email_log = EmailLog.objects.get(email_type="account_deletion_request")
        self.assertEqual(email_log.user, user)
        self.assertEqual(email_log.recipient_email, "team@aishippinglabs.com")
        self.assertEqual(
            email_log.subject,
            f"Account deletion request — user {user.pk} — {user.email}",
        )
        self.assertEqual(email_log.dedupe_key, f"account-deletion-request:{audit.pk}")
        self.assertEqual(audit.row_count_summary, {"email_log_id": email_log.pk})

        args, kwargs = send_ses.call_args
        self.assertEqual(args[0], "team@aishippinglabs.com")
        self.assertEqual(kwargs["cc"], [user.email])
        self.assertEqual(kwargs["email_type"], "account_deletion_request")
        rendered = args[2]
        self.assertIn(user.email, rendered)
        self.assertIn(f"Support ID <strong>{user.pk}</strong>", rendered)
        self.assertIn(f"/studio/users/{user.pk}/", rendered)
        self.assertIn("No account deletion has happened yet", rendered)
        self.assertIn("no later than one month after receipt", rendered)
        self.assertNotIn("verify your email", rendered.lower())
        self.assertNotIn("unsubscribe", rendered.lower())

        self.assertEqual(classify_email_type("account_deletion_request"), EMAIL_KIND_TRANSACTIONAL)
        self.assertIn("account_deletion_request", EMAIL_TYPES_WITHOUT_VERIFY_FOOTER)

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-idempotent-1398",
    )
    def test_repeated_posts_keep_one_request_and_one_email(self, send_ses):
        user = self._user(email="repeat@example.com")
        self.client.force_login(user)

        first = self.client.post("/account/api/request-deletion")
        second = self.client.post("/account/api/request-deletion")
        page = self.client.get("/account/")

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            PrivacyRequestLog.objects.filter(
                request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
                old_user_id=user.pk,
            ).count(),
            1,
        )
        self.assertEqual(EmailLog.objects.filter(user=user).count(), 1)
        send_ses.assert_called_once()
        self.assertContains(page, 'data-testid="privacy-request-received"')
        self.assertContains(page, user.email)
        self.assertContains(page, "no later than one month")
        self.assertContains(page, "team@aishippinglabs.com")
        self.assertNotContains(page, 'data-testid="privacy-request-submit"')

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-configured-recipient-1398",
    )
    @patch(
        "accounts.services.privacy.get_config",
        return_value="privacy-ops@example.com",
    )
    def test_request_uses_validated_configured_team_recipient(
        self,
        _config,
        send_ses,
    ):
        user = self._user(email="configured-requester@example.com")
        self.client.force_login(user)

        response = self.client.post("/account/api/request-deletion")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            EmailLog.objects.get().recipient_email,
            "privacy-ops@example.com",
        )
        args, kwargs = send_ses.call_args
        self.assertEqual(args[0], "privacy-ops@example.com")
        self.assertEqual(kwargs["cc"], [user.email])

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        return_value="ses-account-types-1398",
    )
    def test_every_authenticated_account_type_can_request_without_mutation(self, send_ses):
        cases = [
            ("newsletter", {"signup_source": SIGNUP_SOURCE_NEWSLETTER, "account_activated": False}),
            ("paid", {"tier": self.basic_tier, "subscription_id": "sub_active"}),
            ("pending", {"tier": self.basic_tier, "subscription_id": "sub_pending", "pending_tier": self.free_tier}),
            ("staff", {"is_staff": True}),
            ("superuser", {"is_staff": True, "is_superuser": True}),
        ]
        for label, attrs in cases:
            with self.subTest(label=label):
                user = self._user(email=f"{label}@example.com", **attrs)
                before = {
                    "tier_id": user.tier_id,
                    "pending_tier_id": user.pending_tier_id,
                    "subscription_id": user.subscription_id,
                    "account_activated": user.account_activated,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                }
                self.client.force_login(user)

                response = self.client.post("/account/api/request-deletion")

                self.assertEqual(response.status_code, 302)
                user.refresh_from_db()
                self.assertEqual(
                    {
                        "tier_id": user.tier_id,
                        "pending_tier_id": user.pending_tier_id,
                        "subscription_id": user.subscription_id,
                        "account_activated": user.account_activated,
                        "is_staff": user.is_staff,
                        "is_superuser": user.is_superuser,
                    },
                    before,
                )
                self.assertTrue(self.client.get("/account/").wsgi_request.user.is_authenticated)

        self.assertEqual(send_ses.call_count, len(cases))

    def test_anonymous_and_csrf_rejections_have_no_side_effects(self):
        anonymous = self.client.post("/account/api/request-deletion")
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(PrivacyRequestLog.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

        user = self._user(email="csrf@example.com")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        rejected = csrf_client.post("/account/api/request-deletion")
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(PrivacyRequestLog.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_retired_member_deletion_routes_do_not_resolve_or_mutate(self):
        user = self._user(email="retired-route@example.com")
        self.client.force_login(user)

        post_response = self.client.post(
            "/account/api/delete-account",
            {"confirm_email": user.email, "current_password": "TestPass123!"},
        )
        result_response = self.client.get("/account/deleted")

        self.assertEqual(post_response.status_code, 404)
        self.assertEqual(result_response.status_code, 404)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(self.client.get("/account/").wsgi_request.user.is_authenticated)
        self.assertEqual(PrivacyRequestLog.objects.count(), 0)

    def test_database_boundary_allows_only_one_active_request(self):
        user = self._user(email="constraint@example.com")
        fields = {
            "request_type": PrivacyRequestLog.REQUEST_DELETION_REQUEST,
            "status": PrivacyRequestLog.STATUS_PENDING_DELIVERY,
            "old_user_id": user.pk,
            "normalized_email_hash": "hash-one",
        }
        PrivacyRequestLog.objects.create(**fields)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PrivacyRequestLog.objects.create(
                **{**fields, "status": PrivacyRequestLog.STATUS_REQUESTED},
            )


class AccountDeletionRequestFailureTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="retry@example.com",
            password="TestPass123!",
        )
        self.client.force_login(self.user)

    @patch("accounts.services.privacy.get_config", return_value="not-an-email")
    def test_invalid_recipient_returns_truthful_503_and_reuses_row_on_retry(self, _config):
        failed = self.client.post("/account/api/request-deletion")

        self.assertEqual(failed.status_code, 503)
        self.assertContains(failed, 'data-testid="privacy-request-error"', status_code=503)
        self.assertContains(failed, 'href="mailto:team@aishippinglabs.com"', status_code=503)
        self.assertContains(failed, 'data-testid="privacy-request-submit"', status_code=503)
        self.assertNotContains(failed, "Deletion request received", status_code=503)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        audit = PrivacyRequestLog.objects.get()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_DELIVERY_FAILED)
        self.assertEqual(audit.row_count_summary, {})
        self.assertEqual(EmailLog.objects.count(), 0)
        failed_attempt_at = timezone.now() - timedelta(days=10)
        PrivacyRequestLog.objects.filter(pk=audit.pk).update(
            requested_at=failed_attempt_at,
        )

        with (
            patch("accounts.services.privacy.get_config", return_value="team@aishippinglabs.com"),
            patch(
                "email_app.services.email_service.EmailService._send_ses",
                return_value="ses-retry-1398",
            ) as send_ses,
        ):
            retried = self.client.post("/account/api/request-deletion")

        self.assertEqual(retried.status_code, 302)
        audit.refresh_from_db()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_REQUESTED)
        self.assertGreater(audit.requested_at, failed_attempt_at)
        self.assertEqual(PrivacyRequestLog.objects.count(), 1)
        self.assertEqual(EmailLog.objects.count(), 1)
        send_ses.assert_called_once()

    @patch(
        "email_app.services.email_service.EmailService._send_ses",
        side_effect=EmailServiceError("private SES exception detail"),
    )
    def test_ses_failure_does_not_expose_exception_or_claim_receipt(self, _send):
        response = self.client.post("/account/api/request-deletion")

        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "private SES exception detail", status_code=503)
        self.assertNotContains(response, "Deletion request received", status_code=503)
        audit = PrivacyRequestLog.objects.get()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_DELIVERY_FAILED)
        self.assertNotIn("private SES exception detail", json.dumps(audit.row_count_summary))
        self.assertEqual(EmailLog.objects.count(), 0)
