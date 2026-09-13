"""A6.2 relay sync tests: contact mirror, handlers, callbacks, double opt-in.

Covers the plan card's three moved behaviors:
- sync_contacts_to_relay (redacted dry run, full run, idempotent re-run);
- the durable job handlers that carry preference and unsubscribe changes
  to Relay after commit;
- the relay_callback_processed receiver that projects bounces and
  suppressions back onto the user;
- the /subscribe double opt-in handoff (subscribe dispatches Relay's
  verification flow, /subscribe/confirm mirrors the verified state).
"""

import datetime
import json
import uuid
from io import StringIO
from unittest.mock import patch

import requests
from community_base.jobs.models import JobIntent
from community_base.jobs.registry import JobContext
from community_base.jobs.runner import PermanentJobError, RetryableJobError
from community_base.mail.models import EmailDelivery
from community_base.mail.relay_contacts import (
    RelayContactsClient,
    RelayContactsError,
)
from community_base.testing import FakeRelay
from community_base.testing.helpers import sync_jobs
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from accounts.utils.tokens import generate_user_action_token
from email_app import relay_sync

User = get_user_model()

HANDLERS = (
    "email_app.relay_sync.sync_contact",
    "email_app.relay_sync.unsubscribe_contact",
    "email_app.relay_sync.request_contact_verification",
)


def job_context():
    return JobContext(
        job_id=uuid.uuid4(),
        correlation_id=None,
        attempt=1,
        worker_id="test",
        lease_token=uuid.uuid4(),
    )


def fake_client(relay):
    # The api key must match FakeRelay's default ("relay-test-key") or every
    # request is rejected with 401 before it reaches the route handlers.
    return RelayContactsClient(
        "http://relay.test", "relay-test-key", relay_sync.RELAY_CLIENT, transport=relay
    )


class RelaySyncMixin(TestCase):
    """Shared fixtures: a FakeRelay-backed contacts client."""

    def setUp(self):
        super().setUp()
        self.relay = FakeRelay()
        patcher = patch.object(
            relay_sync, "contacts_client", return_value=fake_client(self.relay)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_user(self, email="member@example.com", **fields):
        return User.objects.create_user(email=email, **fields)

    def make_delivery(self, user):
        return EmailDelivery.objects.create(
            idempotency_key=f"relay-sync-test:{uuid.uuid4()}",
            purpose="newsletter",
            template_key="newsletter",
            recipient_email=user.email,
            context_hash="0" * 64,
            recipient_user=user,
        )

    def emit_callback(self, **overrides):
        from community_base.mail.signals import relay_callback_processed

        kwargs = {
            "sender": EmailDelivery,
            "event_id": str(uuid.uuid4()),
            "event_type": "delivery.bounced",
            "state": "hard_bounced",
            "reason_code": "hard_bounce",
            "sequence": 1,
            "occurred_at": None,
            "delivery": None,
            "created": True,
            "applied": True,
        }
        kwargs.update(overrides)
        relay_callback_processed.send(**kwargs)


class ContactSyncTest(RelaySyncMixin):
    def test_sync_user_upserts_subscription_and_verification(self):
        user = self.make_user(unsubscribed=True)
        contact = relay_sync.sync_user_to_relay(user, fake_client(self.relay))
        self.assertTrue(contact.exists)
        status = fake_client(self.relay).contact_status(user.email, "aisl")
        self.assertEqual(status.client_subscription["status"], "unsubscribed")
        self.assertFalse(status.verified)

    def test_sync_contact_handler_upserts_and_unknown_user_is_noop(self):
        user = self.make_user()
        relay_sync.sync_contact(job_context(), {"user_id": user.pk})
        self.assertEqual(len(self.relay.calls), 1)
        relay_sync.sync_contact(job_context(), {"user_id": user.pk + 1000})
        self.assertEqual(len(self.relay.calls), 1)

    def test_invalid_payload_is_permanent(self):
        with self.assertRaises(PermanentJobError):
            relay_sync.sync_contact(job_context(), {"user_id": "nope"})

    def test_relay_errors_map_onto_job_error_classes(self):
        user = self.make_user()
        retryable = RelayContactsError("relay_http_error", retryable=True)
        with patch.object(relay_sync, "contacts_client", side_effect=retryable):
            with self.assertRaises(RetryableJobError):
                relay_sync.sync_contact(job_context(), {"user_id": user.pk})
        permanent = RelayContactsError("relay_validation")
        with patch.object(relay_sync, "contacts_client", side_effect=permanent):
            with self.assertRaises(PermanentJobError):
                relay_sync.sync_contact(job_context(), {"user_id": user.pk})

    def test_dispatch_creates_one_durable_intent_per_handler(self):
        from django.db import transaction

        user = self.make_user()
        with transaction.atomic():
            relay_sync.dispatch_contact_sync(user.pk)
            relay_sync.dispatch_unsubscribe(user.pk)
            relay_sync.dispatch_request_verification(user.pk)
        for handler in HANDLERS:
            self.assertTrue(
                JobIntent.objects.filter(handler=handler).exists(), handler
            )

    def test_unsubscribe_handler_uses_audience_scope(self):
        user = self.make_user()
        relay_sync.unsubscribe_contact(job_context(), {"user_id": user.pk})
        method, url, kwargs = self.relay.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("/api/subscriptions/unsubscribe", url)
        self.assertEqual(kwargs["json"]["scope"], "audience")

    def test_request_verification_handler_asks_relay_once(self):
        user = self.make_user()
        relay_sync.request_contact_verification(job_context(), {"user_id": user.pk})
        self.assertEqual(len(self.relay.verification_sends), 1)
        send = self.relay.verification_sends[0]
        self.assertEqual(send["template_key"], relay_sync.DOUBLE_OPT_IN_TEMPLATE_KEY)
        self.assertEqual(send["context"]["category"], "newsletter")
        verified = self.make_user(email="done@example.com", email_verified=True)
        relay_sync.request_contact_verification(job_context(), {"user_id": verified.pk})
        self.assertEqual(len(self.relay.verification_sends), 1)


class ConfirmSubscriptionTest(RelaySyncMixin):
    def mint_token(self, email):
        client = fake_client(self.relay)
        client.request_verification(
            email,
            relay_sync.RELAY_AUDIENCE,
            category=relay_sync.DOUBLE_OPT_IN_CATEGORY,
            template_key=relay_sync.DOUBLE_OPT_IN_TEMPLATE_KEY,
        )
        return self.relay.verification_sends[-1]["context"]["verification_token"]

    def test_confirmed_mirrors_verified_state(self):
        user = self.make_user(
            unsubscribed=True,
            verification_expires_at=timezone.now() + datetime.timedelta(days=1),
        )
        outcome, user_id = relay_sync.confirm_subscription(self.mint_token(user.email))
        self.assertEqual(outcome, "confirmed")
        self.assertEqual(user_id, user.pk)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_preferences["newsletter"])
        self.assertIsNone(user.verification_expires_at)
        self.assertTrue(
            JobIntent.objects.filter(
                handler="email_app.relay_sync.sync_contact"
            ).exists()
        )

    def test_invalid_token(self):
        outcome, user_id = relay_sync.confirm_subscription("bogus-token")
        self.assertEqual(outcome, "invalid")
        self.assertIsNone(user_id)

    def test_unknown_user_after_purge(self):
        outcome, user_id = relay_sync.confirm_subscription(
            self.mint_token("ghost@example.com")
        )
        self.assertEqual(outcome, "unknown_user")
        self.assertIsNone(user_id)

    def test_relay_timeout_is_unavailable_not_invalid(self):
        self.relay.error = requests.Timeout("boom")
        outcome, user_id = relay_sync.confirm_subscription("whatever")
        self.assertEqual(outcome, "unavailable")
        self.assertIsNone(user_id)

    def test_unconfigured_client_is_unavailable(self):
        from django.core.exceptions import ImproperlyConfigured

        with patch.object(
            relay_sync, "contacts_client", side_effect=ImproperlyConfigured("x")
        ):
            outcome, user_id = relay_sync.confirm_subscription("whatever")
        self.assertEqual(outcome, "unavailable")
        self.assertIsNone(user_id)


class SubscribeConfirmPageTest(RelaySyncMixin):
    def mint_token(self, email):
        client = fake_client(self.relay)
        client.request_verification(
            email,
            relay_sync.RELAY_AUDIENCE,
            category=relay_sync.DOUBLE_OPT_IN_CATEGORY,
            template_key=relay_sync.DOUBLE_OPT_IN_TEMPLATE_KEY,
        )
        return self.relay.verification_sends[-1]["context"]["verification_token"]

    def test_confirm_page_mirrors_and_confirms(self):
        user = self.make_user(unsubscribed=True)
        response = self.client.get(
            f"/subscribe/confirm?token={self.mint_token(user.email)}"
        )
        self.assertContains(response, "subscribed")
        self.assertTemplateUsed(response, "email_app/unsubscribe_result.html")
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertFalse(user.unsubscribed)

    def test_confirm_page_invalid_token_gets_the_shared_failure_page(self):
        response = self.client.get("/subscribe/confirm?token=bogus")
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "email_app/unsubscribe_result.html")
        self.assertContains(response, "invalid or has expired", status_code=400)

    def test_confirm_page_unknown_user_names_the_purge(self):
        response = self.client.get(
            f"/subscribe/confirm?token={self.mint_token('ghost@example.com')}"
        )
        self.assertEqual(response.status_code, 410)

    def test_confirm_page_relay_outage_asks_to_retry(self):
        self.relay.error = requests.Timeout("boom")
        response = self.client.get("/subscribe/confirm?token=whatever")
        self.assertEqual(response.status_code, 503)


class CallbackProjectionTest(RelaySyncMixin):
    def test_hard_bounce_marks_permanent(self):
        user = self.make_user()
        delivery = self.make_delivery(user)
        self.emit_callback(delivery=delivery)
        user.refresh_from_db()
        self.assertEqual(user.bounce_state, User.BounceState.PERMANENT)
        self.assertTrue(user.unsubscribed)

    def test_soft_bounce_increments_once_per_event(self):
        user = self.make_user()
        delivery = self.make_delivery(user)
        self.emit_callback(
            delivery=delivery,
            event_type="delivery.bounced",
            state="retryable",
            reason_code="soft_bounce",
        )
        user.refresh_from_db()
        self.assertEqual(user.soft_bounce_count, 1)
        self.assertEqual(user.bounce_state, User.BounceState.SOFT)
        # A replayed callback (created=False) must not double-count.
        self.emit_callback(
            delivery=delivery,
            event_type="delivery.bounced",
            state="retryable",
            reason_code="soft_bounce",
            created=False,
        )
        user.refresh_from_db()
        self.assertEqual(user.soft_bounce_count, 1)

    def test_suppression_unsubscribes_and_mirrors_the_preference(self):
        user = self.make_user()
        delivery = self.make_delivery(user)
        self.emit_callback(
            delivery=delivery,
            event_type="delivery.suppressed",
            state="suppressed",
            reason_code="audience_unsubscribe",
        )
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_preferences["newsletter"])

    def test_subscription_changed_is_recorded_only(self):
        user = self.make_user()
        delivery = self.make_delivery(user)
        self.emit_callback(
            delivery=delivery,
            event_type="subscription.changed",
            state="",
            reason_code="unsubscribed",
        )
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)

    def test_callback_without_a_recipient_user_is_ignored(self):
        user = self.make_user()
        self.emit_callback(delivery=None)
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)


class SubscribeRelayHandoffTest(RelaySyncMixin):
    def test_subscribe_dispatches_relay_verification(self):
        with sync_jobs(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/subscribe",
                data=json.dumps({"email": "fresh@example.com"}),
                content_type="application/json",
            )
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(len(self.relay.verification_sends), 1)
        self.assertTrue(
            JobIntent.objects.filter(
                handler="email_app.relay_sync.request_contact_verification"
            ).exists()
        )

    def test_unsubscribe_dispatches_audience_unsubscribe(self):
        user = self.make_user()
        token = generate_user_action_token(user.pk, "unsubscribe")
        with sync_jobs(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(f"/api/unsubscribe?token={token}")
        self.assertContains(response, "unsubscribed")
        self.assertTrue(
            any(
                "/api/subscriptions/unsubscribe" in call[1]
                for call in self.relay.calls
            )
        )


class AccountPreferencesRelaySyncTest(RelaySyncMixin):
    def test_newsletter_toggle_dispatches_contact_sync(self):
        user = self.make_user()
        self.client.force_login(user)
        with sync_jobs(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/account/api/email-preferences",
                data=json.dumps({"newsletter": False}),
                content_type="application/json",
            )
        self.assertEqual(response.json()["status"], "ok")
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertTrue(
            any("/api/contacts" in call[1] for call in self.relay.calls)
        )


class SyncContactsToRelayCommandTest(RelaySyncMixin):
    def test_dry_run_prints_a_redacted_count_and_no_requests(self):
        self.make_user()
        self.make_user(email="second@example.com")
        out = StringIO()
        call_command("sync_contacts_to_relay", "--dry-run", stdout=out)
        text = out.getvalue()
        self.assertIn("would sync 2 contacts", text)
        self.assertNotIn("@", text)
        self.assertEqual(self.relay.calls, [])

    def test_run_syncs_every_user_and_a_rerun_creates_no_new_contacts(self):
        self.make_user()
        self.make_user(email="second@example.com")
        out = StringIO()
        call_command("sync_contacts_to_relay", stdout=out)
        self.assertIn("synced 2 contacts", out.getvalue())
        self.assertEqual(len(self.relay.contacts), 2)
        call_command("sync_contacts_to_relay", stdout=StringIO())
        self.assertEqual(len(self.relay.contacts), 2)

    def test_relay_failure_stops_with_a_resume_point(self):
        for index in range(3):
            self.make_user(email=f"u{index}@example.com")
        self.relay.error = requests.Timeout("boom")
        with self.assertRaises(CommandError) as raised:
            call_command("sync_contacts_to_relay", stdout=StringIO())
        self.assertIn("resume with --after-id 0", str(raised.exception))
