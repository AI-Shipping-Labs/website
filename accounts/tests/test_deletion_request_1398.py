import json
from datetime import timedelta
from unittest.mock import patch

from community_base.jobs.runner import PermanentJobError
from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, tag
from django.utils import timezone

from accounts.models import SIGNUP_SOURCE_NEWSLETTER, PrivacyRequestLog, User
from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)
from email_app.services.email_service import EMAIL_TYPES_WITHOUT_VERIFY_FOOTER
from email_app.testing import StubSESClient, deliver_pending_mail
from tests.fixtures import TierSetupMixin, set_membership


def _stub_send(stub):
    """Return ``(to, cc, html)`` for the single captured SES send."""
    to_addresses = stub.calls[0]["Destination"]["ToAddresses"]
    cc_addresses = stub.calls[0]["Destination"].get("CcAddresses", [])
    html = stub.calls[0]["Content"]["Simple"]["Body"]["Html"]["Data"]
    return to_addresses, cc_addresses, html


class _FailingSESClient(StubSESClient):
    """SES stand-in whose transport always fails."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        raise self.error


@tag("core")
class AccountDeletionRequestViewTest(TierSetupMixin, TestCase):
    def _user(self, email="requester@example.com", **kwargs):
        user = User.objects.create_user(email=email, password="TestPass123!", **kwargs)
        set_membership(user, tier=kwargs.pop("tier", self.free_tier))
        return user

    def test_request_sends_one_transactional_team_message_with_visible_cc(self):
        user = self._user(
            email="canonical@example.com",
            email_verified=False,
            unsubscribed=True,
        )
        self.client.force_login(user)

        stub = StubSESClient()
        with patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=stub,
        ):
            response = self.client.post(
                "/account/api/request-deletion",
                {"email": "attacker@example.com", "user_id": 999999},
                REMOTE_ADDR="192.0.2.10",
                HTTP_USER_AGENT="privacy-test-agent",
            )
            # A1.2: rendering happens in the delivery worker; drain the
            # pending delivery, then assert the audit row it wrote.
            deliver_pending_mail()

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

        delivery = EmailDelivery.objects.get(purpose="account_deletion_request")
        self.assertEqual(delivery.recipient_user, user)
        self.assertEqual(
            delivery.idempotency_key, f"account-deletion-request:{audit.pk}",
        )
        email_log = EmailLog.objects.get(email_type="account_deletion_request")
        self.assertEqual(email_log.user, user)
        self.assertEqual(email_log.recipient_email, "team@aishippinglabs.com")
        self.assertEqual(
            email_log.subject,
            f"Account deletion request — user {user.pk} — {user.email}",
        )
        self.assertEqual(email_log.dedupe_key, f"account-deletion-request:{audit.pk}")
        self.assertEqual(audit.row_count_summary, {"email_log_id": str(delivery.pk)})

        to_addresses, cc_addresses, rendered = _stub_send(stub)
        self.assertEqual(to_addresses, ["team@aishippinglabs.com"])
        self.assertEqual(cc_addresses, [user.email])
        self.assertIn(user.email, rendered)
        self.assertIn(f"Support ID <strong>{user.pk}</strong>", rendered)
        self.assertIn(f"/studio/users/{user.pk}/", rendered)
        self.assertIn(
            f'href="https://aishippinglabs.com/studio/users/{user.pk}/'
            '#privacy-deletion-request"',
            rendered,
        )
        self.assertNotIn("testserver", rendered)
        self.assertIn("No account deletion has happened yet", rendered)
        self.assertIn("no later than one month after receipt", rendered)
        self.assertNotIn("verify your email", rendered.lower())
        self.assertNotIn("unsubscribe", rendered.lower())

        self.assertEqual(classify_email_type("account_deletion_request"), EMAIL_KIND_TRANSACTIONAL)
        self.assertIn("account_deletion_request", EMAIL_TYPES_WITHOUT_VERIFY_FOOTER)

    def test_repeated_posts_keep_one_request_and_one_email(self):
        user = self._user(email="repeat@example.com")
        self.client.force_login(user)
        stub = StubSESClient()

        with patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=stub,
        ):
            first = self.client.post("/account/api/request-deletion")
            second = self.client.post("/account/api/request-deletion")
            page = self.client.get("/account/")
            # The duplicate post resolves to the same audit row before
            # any send, so exactly one delivery exists.
            deliver_pending_mail()

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            PrivacyRequestLog.objects.filter(
                request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
                old_user_id=user.pk,
            ).count(),
            1,
        )
        self.assertEqual(EmailDelivery.objects.count(), 1)
        self.assertEqual(EmailLog.objects.filter(user=user).count(), 1)
        self.assertEqual(len(stub.calls), 1)
        self.assertContains(page, 'data-testid="privacy-request-received"')
        self.assertContains(page, user.email)
        self.assertContains(page, "no later than one month")
        self.assertContains(page, "team@aishippinglabs.com")
        self.assertNotContains(page, 'data-testid="privacy-request-submit"')

    @patch(
        "accounts.services.privacy.get_config",
        return_value="privacy-ops@example.com",
    )
    def test_request_uses_validated_configured_team_recipient(self, _config):
        user = self._user(email="configured-requester@example.com")
        self.client.force_login(user)
        stub = StubSESClient()

        with patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=stub,
        ):
            response = self.client.post("/account/api/request-deletion")
            deliver_pending_mail()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            EmailLog.objects.get().recipient_email,
            "privacy-ops@example.com",
        )
        to_addresses, cc_addresses, _rendered = _stub_send(stub)
        self.assertEqual(to_addresses, ["privacy-ops@example.com"])
        self.assertEqual(cc_addresses, [user.email])

    def test_every_authenticated_account_type_can_request_without_mutation(self):
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
                    "tier_id": user.membership.tier_id,
                    "pending_tier_id": user.membership.pending_tier_id,
                    "subscription_id": user.membership.subscription_id,
                    "account_activated": user.account_activated,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                }
                self.client.force_login(user)
                stub = StubSESClient()

                with patch(
                    "community_base.mail.backends.ses_local.configured_client",
                    return_value=stub,
                ):
                    response = self.client.post("/account/api/request-deletion")
                    deliver_pending_mail()

                self.assertEqual(response.status_code, 302)
                user.refresh_from_db()
                self.assertEqual(
                    {
                        "tier_id": user.membership.tier_id,
                        "pending_tier_id": user.membership.pending_tier_id,
                        "subscription_id": user.membership.subscription_id,
                        "account_activated": user.account_activated,
                        "is_staff": user.is_staff,
                        "is_superuser": user.is_superuser,
                    },
                    before,
                )
                self.assertTrue(self.client.get("/account/").wsgi_request.user.is_authenticated)
                self.assertEqual(len(stub.calls), 1)

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
        self.assertEqual(EmailDelivery.objects.count(), 0)
        failed_attempt_at = timezone.now() - timedelta(days=10)
        PrivacyRequestLog.objects.filter(pk=audit.pk).update(
            requested_at=failed_attempt_at,
        )

        stub = StubSESClient()
        with (
            patch("accounts.services.privacy.get_config", return_value="team@aishippinglabs.com"),
            patch(
                "community_base.mail.backends.ses_local.configured_client",
                return_value=stub,
            ),
        ):
            retried = self.client.post("/account/api/request-deletion")
            deliver_pending_mail()

        self.assertEqual(retried.status_code, 302)
        audit.refresh_from_db()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_REQUESTED)
        self.assertGreater(audit.requested_at, failed_attempt_at)
        self.assertEqual(PrivacyRequestLog.objects.count(), 1)
        self.assertEqual(EmailLog.objects.count(), 1)
        self.assertEqual(len(stub.calls), 1)

    def test_local_send_refusal_does_not_expose_error_or_claim_receipt(self):
        with patch(
            "email_app.package_mail.package_send",
            side_effect=MailError("private local refusal detail"),
        ):
            response = self.client.post("/account/api/request-deletion")

        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "private local refusal detail", status_code=503)
        self.assertNotContains(response, "Deletion request received", status_code=503)
        audit = PrivacyRequestLog.objects.get()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_DELIVERY_FAILED)
        self.assertNotIn(
            "private local refusal detail", json.dumps(audit.row_count_summary),
        )
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_transport_failure_lands_on_delivery_without_audit_row(self):
        # A1.2: the request no longer fails on SES trouble — the durable
        # delivery is accepted and the worker owns the transport outcome.
        # A dead delivery leaves the audit row STATUS_REQUESTED (the
        # member did their part) with no EmailLog receipt.
        stub = _FailingSESClient(RuntimeError("private SES exception detail"))

        with patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=stub,
        ):
            response = self.client.post("/account/api/request-deletion")
            self.assertEqual(response.status_code, 302)
            audit = PrivacyRequestLog.objects.get()
            self.assertEqual(audit.status, PrivacyRequestLog.STATUS_REQUESTED)

            delivery = EmailDelivery.objects.get(
                purpose="account_deletion_request",
            )
            with self.assertRaises(PermanentJobError):
                deliver_pending_mail()

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, EmailDelivery.State.DEAD)
        self.assertEqual(EmailLog.objects.count(), 0)
        self.assertNotIn(
            "private SES exception detail", json.dumps(audit.row_count_summary),
        )
