"""Tests for Calendly booked-call capture (issue #884, Phase 2).

Covers the webhook endpoint, signature verification, idempotency, and
the host-capacity bookkeeping that keeps ``/request-a-call`` availability
accurate without manual staff edits.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import EmailAlias
from community.models import STATUS_BOOKED, STATUS_CANCELED, BookedCall, CallHost
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, WebhookLog

User = get_user_model()

WEBHOOK_URL = '/api/webhooks/calendly'
HOST_URL = 'https://calendly.com/alexey-test/intro'
SIGNING_KEY = 'test-signing-key'


def _payload(event, *, email, host_url=HOST_URL, event_uri=None, invitee_uri=None,
             start_time='2099-01-15T15:00:00.000000Z', name='Alice Member'):
    """Build a minimal but realistic Calendly webhook payload."""
    event_uri = event_uri or 'https://api.calendly.com/scheduled_events/EVT123'
    invitee_uri = invitee_uri or 'https://api.calendly.com/scheduled_events/EVT123/invitees/INV1'
    return {
        'event': event,
        'payload': {
            'email': email,
            'name': name,
            'uri': invitee_uri,
            'scheduling_url': host_url,
            'reschedule_url': 'https://calendly.com/reschedule/abc',
            'cancel_url': 'https://calendly.com/cancellations/abc',
            'scheduled_event': {
                'uri': event_uri,
                'start_time': start_time,
            },
        },
    }


def _signature_header(body_bytes, *, key=SIGNING_KEY, timestamp=None):
    timestamp = timestamp or str(int(time.time()))
    signed = f'{timestamp}.{body_bytes.decode("utf-8")}'
    digest = hmac.new(key.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return f't={timestamp},v1={digest}'


@tag('core')
class CalendlyWebhookCaptureTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(email='alice@test.com', password='pw')
        cls.host = CallHost.objects.create(
            name='Alexey', slug='alexey-cal', booking_url=HOST_URL,
            is_active=True, capacity=2, current_load=0,
        )

    def setUp(self):
        IntegrationSetting.objects.update_or_create(
            key='CALENDLY_WEBHOOK_SIGNING_KEY',
            defaults={'value': SIGNING_KEY, 'group': 'calendly'},
        )
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _post(self, payload):
        """POST a provider-authenticated payload."""
        body = json.dumps(payload).encode()
        return self.client.post(
            WEBHOOK_URL,
            data=body,
            content_type='application/json',
            HTTP_CALENDLY_WEBHOOK_SIGNATURE=_signature_header(body),
        )

    def test_invitee_created_records_booked_call_for_member(self):
        resp = self._post(_payload('invitee.created', email='alice@test.com'))
        self.assertEqual(resp.status_code, 200)
        call = BookedCall.objects.get(calendly_event_uri__endswith='EVT123')
        self.assertEqual(call.member, self.member)
        self.assertEqual(call.status, STATUS_BOOKED)
        self.assertEqual(call.host, self.host)
        self.assertIsNotNone(call.scheduled_at)

    def test_invitee_created_increments_host_load(self):
        self._post(_payload('invitee.created', email='alice@test.com'))
        self.host.refresh_from_db()
        self.assertEqual(self.host.current_load, 1)

    def test_invitee_canceled_decrements_host_load_and_marks_canceled(self):
        self._post(_payload('invitee.created', email='alice@test.com'))
        self._post(_payload('invitee.canceled', email='alice@test.com'))
        self.host.refresh_from_db()
        self.assertEqual(self.host.current_load, 0)
        call = BookedCall.objects.get(calendly_event_uri__endswith='EVT123')
        self.assertEqual(call.status, STATUS_CANCELED)
        self.assertIsNotNone(call.canceled_at)

    def test_duplicate_invitee_created_is_idempotent(self):
        """A re-delivered webhook must not double-count capacity."""
        self._post(_payload('invitee.created', email='alice@test.com'))
        self._post(_payload('invitee.created', email='alice@test.com'))
        self.host.refresh_from_db()
        self.assertEqual(self.host.current_load, 1)
        self.assertEqual(
            BookedCall.objects.filter(calendly_event_uri__endswith='EVT123').count(),
            1,
        )

    def test_duplicate_cancel_does_not_underflow_load(self):
        self._post(_payload('invitee.created', email='alice@test.com'))
        self._post(_payload('invitee.canceled', email='alice@test.com'))
        self._post(_payload('invitee.canceled', email='alice@test.com'))
        self.host.refresh_from_db()
        self.assertEqual(self.host.current_load, 0)

    def test_unmatched_email_still_records_call_without_member(self):
        self._post(_payload('invitee.created', email='stranger@example.com'))
        call = BookedCall.objects.get(calendly_event_uri__endswith='EVT123')
        self.assertIsNone(call.member)
        self.assertEqual(call.invitee_email, 'stranger@example.com')

    def test_unmatched_host_is_durable_without_capacity_change(self):
        self._post(_payload(
            'invitee.created', email='alice@test.com',
            host_url='https://calendly.com/unknown/event?month=2099-01',
        ))
        call = BookedCall.objects.get(calendly_event_uri__endswith='EVT123')
        self.assertIsNone(call.host)
        self.host.refresh_from_db()
        self.assertEqual(self.host.current_load, 0)

    def test_host_url_query_and_trailing_slash_are_normalized(self):
        self._post(_payload(
            'invitee.created', email='alice@test.com',
            host_url=f'{HOST_URL}/?month=2099-01',
        ))
        self.assertEqual(BookedCall.objects.get().host, self.host)

    def test_alias_email_links_canonical_active_member(self):
        EmailAlias.objects.create(user=self.member, email='relay@test.com')
        self._post(_payload('invitee.created', email='relay@test.com'))
        self.assertEqual(BookedCall.objects.get().member, self.member)

    def test_cancel_before_create_leaves_terminal_tombstone(self):
        payload = _payload('invitee.canceled', email='alice@test.com')
        self._post(payload)
        self._post(_payload('invitee.created', email='alice@test.com'))
        call = BookedCall.objects.get()
        self.assertEqual(call.status, STATUS_CANCELED)
        self.host.refresh_from_db()
        self.assertEqual(self.host.current_load, 0)

    def test_processing_error_returns_500_and_delivery_can_retry(self):
        payload = _payload('invitee.created', email='alice@test.com')
        body = json.dumps(payload).encode()
        header = _signature_header(body)
        with patch(
            'integrations.services.calendly_delivery.process_webhook',
            side_effect=RuntimeError('temporary'),
        ):
            failed = self.client.post(
                WEBHOOK_URL, data=body, content_type='application/json',
                HTTP_CALENDLY_WEBHOOK_SIGNATURE=header,
            )
        self.assertEqual(failed.status_code, 500)
        log = WebhookLog.objects.get(service='calendly')
        self.assertFalse(log.processed)
        self.assertEqual(
            log.error_message,
            'Delivery processing failed (processing_error). Retry is safe.',
        )
        retried = self.client.post(
            WEBHOOK_URL, data=body, content_type='application/json',
            HTTP_CALENDLY_WEBHOOK_SIGNATURE=header,
        )
        self.assertEqual(retried.status_code, 200)
        log.refresh_from_db()
        self.assertTrue(log.processed)
        self.assertEqual(log.attempts, 2)

    def test_identical_signed_replay_does_not_process_twice(self):
        payload = _payload('invitee.created', email='alice@test.com')
        self._post(payload)
        self._post(payload)
        self.assertEqual(WebhookLog.objects.filter(service='calendly').count(), 1)

    def test_webhook_is_logged(self):
        self._post(_payload('invitee.created', email='alice@test.com'))
        log = WebhookLog.objects.get(service='calendly')
        self.assertEqual(log.event_type, 'invitee.created')
        self.assertTrue(log.processed)

    def test_unhandled_event_type_is_ignored(self):
        resp = self._post(_payload('routing_form_submission.created', email='alice@test.com'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(BookedCall.objects.exists())

    def test_malformed_json_returns_400(self):
        body = b'not-json'
        resp = self.client.post(
            WEBHOOK_URL, data=body, content_type='application/json',
            HTTP_CALENDLY_WEBHOOK_SIGNATURE=_signature_header(body),
        )
        self.assertEqual(resp.status_code, 400)


@tag('core')
class CalendlyWebhookSignatureTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = CallHost.objects.create(
            name='Alexey', slug='alexey-cal', booking_url=HOST_URL,
            is_active=True, capacity=2, current_load=0,
        )

    def tearDown(self):
        clear_config_cache()

    def _enable_validation(self):
        IntegrationSetting.objects.create(
            key='CALENDLY_WEBHOOK_SIGNING_KEY', value=SIGNING_KEY, group='calendly',
        )
        clear_config_cache()

    def test_valid_signature_is_accepted(self):
        self._enable_validation()
        payload = _payload('invitee.created', email='alice@test.com')
        body = json.dumps(payload).encode()
        resp = self.client.post(
            WEBHOOK_URL, data=body, content_type='application/json',
            HTTP_CALENDLY_WEBHOOK_SIGNATURE=_signature_header(body),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(BookedCall.objects.exists())

    def test_missing_signature_is_rejected_when_validation_enabled(self):
        self._enable_validation()
        payload = _payload('invitee.created', email='alice@test.com')
        resp = self.client.post(
            WEBHOOK_URL, data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(BookedCall.objects.exists())

    def test_bad_signature_is_rejected(self):
        self._enable_validation()
        payload = _payload('invitee.created', email='alice@test.com')
        body = json.dumps(payload).encode()
        resp = self.client.post(
            WEBHOOK_URL, data=body, content_type='application/json',
            HTTP_CALENDLY_WEBHOOK_SIGNATURE=f't={int(time.time())},v1=deadbeef',
        )
        self.assertEqual(resp.status_code, 400)

    def test_unset_key_rejects_unsigned_fail_closed(self):
        clear_config_cache()
        payload = _payload('invitee.created', email='alice@test.com')
        resp = self.client.post(
            WEBHOOK_URL, data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_stale_valid_signature_is_rejected(self):
        self._enable_validation()
        payload = _payload('invitee.created', email='alice@test.com')
        body = json.dumps(payload).encode()
        resp = self.client.post(
            WEBHOOK_URL, data=body, content_type='application/json',
            HTTP_CALENDLY_WEBHOOK_SIGNATURE=_signature_header(
                body, timestamp=str(int(time.time()) - 301),
            ),
        )
        self.assertEqual(resp.status_code, 400)
