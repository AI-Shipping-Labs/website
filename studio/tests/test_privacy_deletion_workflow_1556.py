"""Authoritative Studio account-deletion workflow coverage for #1556."""

import json
from unittest.mock import ANY, Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, tag
from django.urls import reverse

from accounts.models import PrivacyCompletionDelivery, PrivacyRequestLog
from accounts.services.privacy import (
    delete_account_for_privacy,
    normalized_privacy_email_hash,
)
from accounts.services.privacy_recipient import decrypt_recipient, encrypt_recipient
from email_app.models import EmailLog
from email_app.relay_transactional import (
    RelayTransactionalError,
    RelayTransactionalResult,
)
from email_app.testing import StubRelayTemplateClient
from tests.fixtures import create_user_with_membership

User = get_user_model()


def relay_config(key, default=""):
    values = {
        "RELAY_BASE_URL": "https://relay.example.test",
        "RELAY_API_KEY": "test-relay-key",
    }
    return values.get(key, default)


def relay_response(request_id, *, status="queued"):
    response = Mock(status_code=202)
    response.json.return_value = {
        "message": {
            "id": f"relay-privacy-{request_id}",
            "status": status,
            "template_key": "account_deletion_completed",
            "idempotency_key": f"account-deletion-completed:{request_id}",
        },
        "idempotent_replay": False,
    }
    return response


@tag("core")
class StudioPrivacyDeletionWorkflowTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            email="privacy-superuser@test.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )
        cls.staff = User.objects.create_user(
            email="privacy-staff@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="privacy-member@test.com",
            password="testpass",
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def accepted_request(self, user=None, **overrides):
        user = user or self.member
        fields = {
            "request_type": PrivacyRequestLog.REQUEST_DELETION_REQUEST,
            "status": PrivacyRequestLog.STATUS_REQUESTED,
            "old_user_id": user.pk,
            "normalized_email_hash": normalized_privacy_email_hash(user.email),
            "email_domain": user.email.rsplit("@", 1)[-1],
        }
        fields.update(overrides)
        return PrivacyRequestLog.objects.create(**fields)

    def confirm(self, request_log, **overrides):
        data = {
            "confirm_email": self.member.email,
            "acknowledge": "yes",
            "workflow_version": request_log.workflow_version,
        }
        data.update(overrides)
        return self.client.post(
            f"/studio/privacy/deletion-requests/{request_log.pk}/confirm",
            data,
        )

    def assert_completion_delivery(self, request_log, subject):
        delivery = request_log.completion_delivery
        self.assertEqual(delivery.status, PrivacyCompletionDelivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(delivery.recipient_ciphertext, "")
        self.assertEqual(delivery.email_log.recipient_email, "[deleted-account]")
        self.assertEqual(delivery.email_log.subject, subject)
        self.assertEqual(
            delivery.email_log.dedupe_key,
            f"account-deletion-completed:{request_log.pk}",
        )

    def assert_completed_request(self, request_log, old_user_id):
        request_log.refresh_from_db()
        self.assertEqual(request_log.status, PrivacyRequestLog.STATUS_COMPLETED)
        audit = request_log.execution_audits.get(
            request_type=PrivacyRequestLog.REQUEST_DELETE,
            status=PrivacyRequestLog.STATUS_COMPLETED,
        )
        self.assertEqual(audit.operator_user_id, self.superuser.pk)
        self.assertEqual(audit.old_user_id, old_user_id)
        self.assertNotIn("privacy-member", json.dumps(audit.row_count_summary))

    def assert_confirmation_wire(self, send_relay, template_client, old_user_id):
        args, kwargs = send_relay.call_args
        self.assertEqual(args[0], "privacy-member@test.com")
        self.assertEqual(args[1], "account_deletion_completed")
        self.assertEqual(kwargs["cc"], ["team@aishippinglabs.com"])
        self.assertEqual(args[2]["support_id"], old_user_id)
        template_client.return_value.preview.assert_called_once_with(
            "account_deletion_completed",
            ANY,
            version=2,
        )

    def test_user_callout_requires_an_accepted_request(self):
        pending = self.accepted_request(
            status=PrivacyRequestLog.STATUS_PENDING_DELIVERY,
        )
        response = self.client.get(f"/studio/users/{self.member.pk}/")
        self.assertNotContains(response, 'data-testid="privacy-deletion-request-callout"')

        pending.status = PrivacyRequestLog.STATUS_REQUESTED
        pending.save(update_fields=["status"])
        response = self.client.get(f"/studio/users/{self.member.pk}/")
        self.assertContains(response, 'id="privacy-deletion-request"')
        self.assertContains(response, str(self.member.pk))
        self.assertContains(
            response,
            f"/studio/privacy/deletion-requests/{pending.pk}/",
        )

    def test_unaccepted_request_cannot_open_the_review_surface(self):
        pending = self.accepted_request(
            status=PrivacyRequestLog.STATUS_PENDING_DELIVERY,
        )
        response = self.client.get(
            f"/studio/privacy/deletion-requests/{pending.pk}/",
        )
        self.assertEqual(response.status_code, 404)

    def test_review_is_staff_readable_but_mutations_are_superuser_only(self):
        request_log = self.accepted_request()
        review_url = f"/studio/privacy/deletion-requests/{request_log.pk}/"
        confirm_url = f"{review_url}confirm".replace("//confirm", "/confirm")
        retry_url = f"{review_url}retry-confirmation".replace(
            "//retry-confirmation",
            "/retry-confirmation",
        )

        anonymous = Client()
        self.assertEqual(anonymous.get(review_url).status_code, 302)
        self.assertEqual(anonymous.post(confirm_url).status_code, 302)

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(review_url).status_code, 403)
        self.assertEqual(self.client.post(confirm_url).status_code, 403)
        self.assertEqual(self.client.post(retry_url).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

        self.client.force_login(self.staff)
        review = self.client.get(review_url)
        self.assertContains(review, "A superuser must complete this deletion request")
        self.assertEqual(self.client.post(confirm_url).status_code, 403)
        self.assertEqual(self.client.post(retry_url).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(confirm_url).status_code, 405)
        self.assertEqual(self.client.get(retry_url).status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.superuser)
        self.assertEqual(csrf_client.post(confirm_url).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.assertEqual(request_log.execution_audits.count(), 0)

    @patch(
        "accounts.services.privacy_delivery.send_relay_transactional",
        return_value=RelayTransactionalResult("relay-completed-1556", "queued", False),
    )
    @patch(
        "email_app.services.relay_templates.configured_template_client",
    )
    def test_canonical_success_links_audit_and_sends_one_scrubbed_confirmation(
        self,
        template_client,
        send_relay,
    ):
        template_client.return_value.preview.return_value = {
            "subject": "Relay account deletion subject",
        }
        request_log = self.accepted_request()
        old_user_id = self.member.pk
        with patch(
            "accounts.services.privacy_workflow.delete_account_for_privacy",
            wraps=delete_account_for_privacy,
        ) as canonical_delete:
            response = self.confirm(request_log)

        self.assertEqual(canonical_delete.call_count, 1)
        self.assertFalse(canonical_delete.call_args.kwargs["notify_staff"])
        self.assertRedirects(
            response,
            f"/studio/privacy/deletion-requests/{request_log.pk}/",
        )
        self.assertFalse(User.objects.filter(pk=old_user_id).exists())
        self.assert_completed_request(request_log, old_user_id)
        self.assert_completion_delivery(
            request_log,
            "Relay account deletion subject",
        )
        self.assert_confirmation_wire(send_relay, template_client, old_user_id)

    @patch("email_app.relay_transactional.is_enabled", return_value=False)
    @patch("email_app.relay_transactional.get_config", side_effect=relay_config)
    def test_relay_request_uses_real_recipient_cc_and_redacted_local_state(
        self,
        _config,
        _enabled,
    ):
        request_log = self.accepted_request()
        old_user_id = self.member.pk
        response = relay_response(request_log.pk)

        with patch(
            "email_app.relay_transactional.requests.post",
            return_value=response,
        ) as post:
            result = self.confirm(request_log)

        self.assertEqual(result.status_code, 302)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["email"], "privacy-member@test.com")
        self.assertEqual(payload["cc"], ["team@aishippinglabs.com"])
        self.assertEqual(payload["template_key"], "account_deletion_completed")
        self.assertEqual(payload["context"]["support_id"], old_user_id)
        delivery = PrivacyCompletionDelivery.objects.get(request=request_log)
        self.assertEqual(delivery.status, PrivacyCompletionDelivery.STATUS_SENT)
        self.assertEqual(delivery.recipient_ciphertext, "")
        self.assertEqual(delivery.email_log.recipient_email, "[deleted-account]")

    @patch("email_app.relay_transactional.is_enabled", return_value=False)
    @patch("email_app.relay_transactional.get_config", side_effect=relay_config)
    def test_relay_timeout_is_fenced_and_diagnostics_are_redacted(
        self,
        _config,
        _enabled,
    ):
        request_log = self.accepted_request()
        recipient = self.member.email

        with self.assertLogs("email_app.relay_transactional", level="ERROR") as logs:
            with patch(
                "email_app.relay_transactional.requests.post",
                side_effect=requests.Timeout(),
            ):
                result = self.confirm(request_log)

        self.assertEqual(result.status_code, 302)
        delivery = PrivacyCompletionDelivery.objects.get(request=request_log)
        self.assertEqual(
            delivery.status,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        )
        diagnostics = " ".join(logs.output)
        self.assertNotIn(recipient, diagnostics)
        self.assertNotIn("test-relay-key", diagnostics)
        self.assertFalse(EmailLog.objects.filter(recipient_email=recipient).exists())

    def test_confirmation_requires_exact_email_and_ack_without_retargeting(self):
        other = User.objects.create_user(email="other-target@test.com", password="testpass")
        request_log = self.accepted_request()

        missing_ack = self.confirm(request_log, acknowledge="", user_id=other.pk)
        self.assertEqual(missing_ack.status_code, 302)
        mismatch = self.confirm(
            request_log,
            confirm_email=other.email,
            user_id=other.pk,
        )
        self.assertEqual(mismatch.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(User.objects.filter(pk=other.pk).exists())
        self.assertFalse(PrivacyCompletionDelivery.objects.exists())

    def test_stale_identity_is_linked_and_neither_posted_user_is_deleted(self):
        request_log = self.accepted_request()
        self.member.email = "changed-login@test.com"
        self.member.save(update_fields=["email"])
        other = User.objects.create_user(email="forged-target@test.com", password="testpass")

        response = self.confirm(
            request_log,
            confirm_email=self.member.email,
            user_id=other.pk,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(User.objects.filter(pk=other.pk).exists())
        audit = request_log.execution_audits.get()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_BLOCKED)
        self.assertEqual(audit.blocker_reason, "identity_changed")
        self.assertEqual(audit.operator_user_id, self.superuser.pk)
        self.assertFalse(PrivacyCompletionDelivery.objects.exists())

    def test_canonical_staff_and_subscription_blockers_remain_retryable(self):
        cases = [
            ("staff", {"is_staff": True}, PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT),
            (
                "subscription",
                {"subscription_id": "sub_active_1556"},
                PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION,
            ),
        ]
        for label, attrs, blocker in cases:
            with self.subTest(label=label):
                target = create_user_with_membership(
                    email=f"{label}-blocked@test.com",
                    password="testpass",
                    **attrs,
                )
                request_log = self.accepted_request(target)
                response = self.confirm(
                    request_log,
                    confirm_email=target.email,
                )
                self.assertEqual(response.status_code, 302)
                self.assertTrue(User.objects.filter(pk=target.pk).exists())
                request_log.refresh_from_db()
                self.assertEqual(request_log.status, PrivacyRequestLog.STATUS_REQUESTED)
                audit = request_log.execution_audits.get()
                self.assertEqual(audit.status, PrivacyRequestLog.STATUS_BLOCKED)
                self.assertEqual(audit.blocker_reason, blocker)
                self.assertEqual(audit.operator_user_id, self.superuser.pk)
                self.assertFalse(
                    PrivacyCompletionDelivery.objects.filter(request=request_log).exists(),
                )

    def test_execution_error_rolls_back_and_stores_only_stable_failure(self):
        request_log = self.accepted_request()

        def mutate_then_raise(target, _summary):
            User.objects.filter(pk=target.pk).update(first_name="must-roll-back")
            raise RuntimeError("member@example.com private database detail")

        with self.assertLogs(
            "accounts.services.privacy_workflow",
            level="ERROR",
        ) as captured_logs:
            with patch(
                "accounts.services.privacy._erase_local_slack_threads",
                side_effect=mutate_then_raise,
            ):
                response = self.confirm(request_log)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())
        self.member.refresh_from_db()
        self.assertEqual(self.member.first_name, "")
        request_log.refresh_from_db()
        self.assertEqual(request_log.status, PrivacyRequestLog.STATUS_REQUESTED)
        audit = request_log.execution_audits.get()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_FAILED)
        self.assertEqual(audit.error_code, "execution_failed")
        self.assertEqual(audit.row_count_summary, {})
        self.assertNotIn("member@example.com", json.dumps(audit.row_count_summary))
        rendered_logs = "\n".join(captured_logs.output)
        self.assertNotIn("member@example.com", rendered_logs)
        self.assertNotIn("private database detail", rendered_logs)
        self.assertFalse(PrivacyCompletionDelivery.objects.exists())

    def test_delivery_intent_failure_rolls_back_the_whole_deletion(self):
        request_log = self.accepted_request()
        old_user_id = self.member.pk

        with self.assertLogs(
            "accounts.services.privacy_workflow",
            level="ERROR",
        ) as captured_logs:
            with patch(
                "accounts.services.privacy_workflow.PrivacyCompletionDelivery.objects.create",
                side_effect=RuntimeError("member@example.com private database detail"),
            ):
                response = self.confirm(request_log)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(pk=old_user_id).exists())
        request_log.refresh_from_db()
        self.assertEqual(request_log.status, PrivacyRequestLog.STATUS_REQUESTED)
        self.assertFalse(
            request_log.execution_audits.filter(status=PrivacyRequestLog.STATUS_COMPLETED).exists(),
        )
        failure = request_log.execution_audits.get()
        self.assertEqual(failure.status, PrivacyRequestLog.STATUS_FAILED)
        self.assertEqual(failure.error_code, "execution_failed")
        self.assertFalse(PrivacyCompletionDelivery.objects.exists())
        rendered_logs = "\n".join(captured_logs.output)
        self.assertNotIn("member@example.com", rendered_logs)
        self.assertNotIn("private database detail", rendered_logs)

    def test_missing_user_without_completed_audit_is_not_success(self):
        request_log = PrivacyRequestLog.objects.create(
            request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
            status=PrivacyRequestLog.STATUS_REQUESTED,
            old_user_id=987654321,
            normalized_email_hash=normalized_privacy_email_hash("gone@test.com"),
            email_domain="test.com",
        )
        review = self.client.get(
            f"/studio/privacy/deletion-requests/{request_log.pk}/",
        )
        self.assertContains(review, "this is not proof of deletion")
        self.assertNotContains(review, 'data-testid="privacy-deletion-form"')
        response = self.client.post(
            f"/studio/privacy/deletion-requests/{request_log.pk}/confirm",
            {
                "confirm_email": "gone@test.com",
                "acknowledge": "yes",
                "workflow_version": request_log.workflow_version,
            },
        )
        self.assertEqual(response.status_code, 302)
        audit = request_log.execution_audits.get()
        self.assertEqual(audit.status, PrivacyRequestLog.STATUS_FAILED)
        self.assertEqual(audit.error_code, "missing_user")
        self.assertFalse(PrivacyCompletionDelivery.objects.exists())

    def test_known_delivery_failure_retries_without_repeating_deletion(self):
        request_log = self.accepted_request()
        old_user_id = self.member.pk
        with patch(
            "accounts.services.privacy_delivery.send_relay_transactional",
            side_effect=RelayTransactionalError("relay_rejected"),
        ):
            self.confirm(request_log)

        delivery = PrivacyCompletionDelivery.objects.get(request=request_log)
        self.assertEqual(delivery.status, PrivacyCompletionDelivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertFalse(User.objects.filter(pk=old_user_id).exists())
        self.assertNotIn("privacy-retry-member", delivery.recipient_ciphertext)
        self.assertEqual(
            decrypt_recipient(delivery.recipient_ciphertext),
            "privacy-member@test.com",
        )

        with patch(
            "accounts.services.privacy_delivery.send_relay_transactional",
            return_value=RelayTransactionalResult("relay-retry-1556", "queued", False),
        ) as send_relay:
            retried = self.client.post(
                f"/studio/privacy/deletion-requests/{request_log.pk}/retry-confirmation",
            )
            replayed = self.client.post(
                f"/studio/privacy/deletion-requests/{request_log.pk}/retry-confirmation",
            )

        self.assertEqual(retried.status_code, 302)
        self.assertEqual(replayed.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PrivacyCompletionDelivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 2)
        self.assertEqual(delivery.recipient_ciphertext, "")
        self.assertEqual(send_relay.call_count, 1)
        self.assertEqual(request_log.execution_audits.filter(status="completed").count(), 1)

    def test_ambiguous_relay_transport_failure_is_fenced_from_retry(self):
        request_log = self.accepted_request()

        with patch(
            "accounts.services.privacy_delivery.send_relay_transactional",
            side_effect=RelayTransactionalError(
                "relay_ack_unknown",
                outcome_unknown=True,
            ),
        ) as send_relay:
            response = self.confirm(request_log)

        self.assertEqual(response.status_code, 302)
        delivery = PrivacyCompletionDelivery.objects.get(request=request_log)
        self.assertEqual(
            delivery.status,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        )
        self.assertEqual(delivery.error_code, "transport_outcome_unknown")
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(send_relay.call_count, 1)

        retry = self.client.post(
            f"/studio/privacy/deletion-requests/{request_log.pk}/retry-confirmation",
        )

        self.assertEqual(retry.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(
            delivery.status,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        )
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(send_relay.call_count, 1)

    def test_relay_rejection_does_not_persist_deleted_recipient(self):
        request_log = self.accepted_request()
        private_sentinel = "deleted-person-1556@example.com"
        with patch(
            "accounts.services.privacy_delivery.send_relay_transactional",
            side_effect=RelayTransactionalError("relay_rejected"),
        ) as send_relay:
            response = self.confirm(request_log)

        self.assertEqual(response.status_code, 302)
        delivery = PrivacyCompletionDelivery.objects.get(request=request_log)
        self.assertEqual(delivery.status, PrivacyCompletionDelivery.STATUS_FAILED)
        self.assertEqual(delivery.error_code, "delivery_failed")
        self.assertEqual(send_relay.call_count, 1)
        self.assertNotIn(private_sentinel, delivery.recipient_ciphertext)
        self.assertFalse(
            EmailLog.objects.filter(recipient_email=private_sentinel).exists(),
        )

    def test_lingering_transport_claim_becomes_unknown_and_never_resends(self):
        request_log = self.accepted_request()
        request_log.status = PrivacyRequestLog.STATUS_COMPLETED
        request_log.save(update_fields=["status"])
        audit = PrivacyRequestLog.objects.create(
            request_type=PrivacyRequestLog.REQUEST_DELETE,
            status=PrivacyRequestLog.STATUS_COMPLETED,
            old_user_id=self.member.pk,
            normalized_email_hash=request_log.normalized_email_hash,
            email_domain=request_log.email_domain,
            originating_request=request_log,
            operator_user_id=self.superuser.pk,
        )
        delivery = PrivacyCompletionDelivery.objects.create(
            request=request_log,
            status=PrivacyCompletionDelivery.STATUS_SENDING,
            recipient_ciphertext="fernet:v1:not-plaintext",
            normalized_email_hash=request_log.normalized_email_hash,
            email_domain=request_log.email_domain,
            dedupe_key=f"account-deletion-completed:{request_log.pk}",
        )

        with patch(
            "accounts.services.privacy_delivery.send_relay_transactional",
        ) as send:
            response = self.client.post(
                f"/studio/privacy/deletion-requests/{request_log.pk}/retry-confirmation",
            )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(
            delivery.status,
            PrivacyCompletionDelivery.STATUS_OUTCOME_UNKNOWN,
        )
        send.assert_not_called()
        self.assertTrue(audit.pk)

    @patch(
        "accounts.services.privacy_delivery.send_relay_transactional",
        return_value=RelayTransactionalResult("relay-once-1556", "queued", False),
    )
    def test_two_tab_replay_converges_on_one_delete_audit_and_email(self, send_relay):
        request_log = self.accepted_request()
        stale_version = request_log.workflow_version
        first = self.confirm(request_log, workflow_version=stale_version)
        second = self.confirm(request_log, workflow_version=stale_version)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            PrivacyRequestLog.objects.filter(
                originating_request=request_log,
                request_type=PrivacyRequestLog.REQUEST_DELETE,
                status=PrivacyRequestLog.STATUS_COMPLETED,
            ).count(),
            1,
        )
        self.assertEqual(PrivacyCompletionDelivery.objects.count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(email_type="account_deletion_completed").count(),
            1,
        )
        self.assertEqual(send_relay.call_count, 1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PrivacyRequestLog.objects.create(
                request_type=PrivacyRequestLog.REQUEST_DELETE,
                status=PrivacyRequestLog.STATUS_COMPLETED,
                old_user_id=request_log.old_user_id,
                normalized_email_hash=request_log.normalized_email_hash,
                originating_request=request_log,
            )


class PrivacyCompletionTemplateTest(TestCase):
    def test_pending_recipient_uses_authenticated_encryption(self):
        ciphertext = encrypt_recipient("recipient@test.com")
        self.assertNotIn("recipient@test.com", ciphertext)
        self.assertEqual(decrypt_recipient(ciphertext), "recipient@test.com")
        tampered_character = "y" if ciphertext[25] == "x" else "x"
        with self.assertRaises(ImproperlyConfigured):
            decrypt_recipient(
                f"{ciphertext[:25]}{tampered_character}{ciphertext[26:]}"
            )

    def test_studio_editor_and_preview_use_synthetic_privacy_context(self):
        superuser = User.objects.create_superuser(
            email="privacy-template-admin@test.com",
            password="testpass",
        )
        self.client.force_login(superuser)
        relay = StubRelayTemplateClient()
        template = relay.template("account_deletion_completed")
        template["subject"] = "Relay account deletion subject"
        relay.save_draft("account_deletion_completed", template)
        relay.calls = []
        with patch(
            "studio.views.email_templates.configured_template_client",
            return_value=relay,
        ):
            edit = self.client.get(
                "/studio/email-templates/account_deletion_completed/edit/",
            )
            preview = self.client.post(
                "/studio/email-templates/account_deletion_completed/preview/",
                {"recipient": "named"},
            )
        self.assertContains(edit, "Relay account deletion subject")
        self.assertContains(preview, "Relay preview")
        preview_call = relay.calls[-1]
        context = preview_call["context"]
        self.assertEqual(context["completed_at_utc"], "2026-08-13 11:00:00 UTC")
        self.assertEqual(context["support_id"], 42)
        self.assertEqual(context["privacy_email"], "team@aishippinglabs.com")
        self.assertNotIn("unsubscribe_url", context)

    def test_no_destructive_privacy_api_exists(self):
        response = self.client.delete("/api/privacy/deletion-requests/1")
        self.assertEqual(response.status_code, 404)

    def test_completion_delivery_admin_is_read_only(self):
        superuser = User.objects.create_superuser(
            email="privacy-forensics-admin@test.com",
            password="testpass",
        )
        request_log = PrivacyRequestLog.objects.create(
            request_type=PrivacyRequestLog.REQUEST_DELETION_REQUEST,
            status=PrivacyRequestLog.STATUS_COMPLETED,
            old_user_id=12345,
            normalized_email_hash="hash-only",
            email_domain="example.com",
        )
        delivery = PrivacyCompletionDelivery.objects.create(
            request=request_log,
            recipient_ciphertext="fernet:v1:ciphertext-only",
            normalized_email_hash="hash-only",
            email_domain="example.com",
            dedupe_key="account-deletion-completed:readonly",
        )
        self.client.force_login(superuser)
        detail_url = reverse(
            "admin:accounts_privacycompletiondelivery_change",
            args=[delivery.pk],
        )
        detail = self.client.get(detail_url)
        self.assertContains(detail, "fernet:v1:ciphertext-only")
        self.assertNotContains(detail, "Delete")
        self.assertEqual(self.client.post(detail_url, {"status": "sent"}).status_code, 403)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PrivacyCompletionDelivery.STATUS_PENDING)
