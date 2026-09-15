"""GitHub webhook delivery deduplication (issue #1433).

GitHub retries a delivery (and operators can redeliver one by hand) with
the same ``X-GitHub-Delivery`` id. Without a claim on that id, every
retry re-queued a content sync and rewrote ``ContentSource`` state.
These tests pin the dedup contract of the package webhook view the site
mounts at ``/api/webhooks/github``: the claim lives on
``WebhookLog.deduplication_key`` as ``github:<sha256(delivery id)>``.
"""

import hashlib
import json
from unittest import mock

from community_base.content_sync.models import WebhookLog as PackageWebhookLog
from django.test import TestCase, tag

from integrations.models import ContentSource, WebhookLog

WEBHOOK_SECRET = 'dedup-github-webhook-secret'
REPO = 'AI-Shipping-Labs/dedup-content'


def sign(body, secret=WEBHOOK_SECRET):
    """Build a valid ``X-Hub-Signature-256`` header for ``body``."""
    if isinstance(body, str):
        body = body.encode('utf-8')
    import hmac
    digest = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f'sha256={digest}'


def hashed_key(delivery_id):
    digest = hashlib.sha256(delivery_id.encode('utf-8')).hexdigest()
    return f'github:{digest}'


@tag('core')
class GitHubWebhookDeduplicationTest(TestCase):
    """A repeated GitHub delivery must be an idempotent no-op."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name=REPO,
            webhook_secret=WEBHOOK_SECRET,
        )

    def _payload(self, ref='refs/heads/main', **extra):
        payload = {
            'ref': ref,
            'repository': {'full_name': REPO, 'default_branch': 'main'},
        }
        payload.update(extra)
        return payload

    def _post(self, payload, delivery_id='11111111-2222-3333-4444-555555555555',
              event_type='push', secret=WEBHOOK_SECRET, signature=None,
              send_delivery_header=True):
        body = json.dumps(payload)
        headers = {
            'HTTP_X_HUB_SIGNATURE_256': (
                signature if signature is not None else sign(body, secret)
            ),
            'HTTP_X_GITHUB_EVENT': event_type,
        }
        if send_delivery_header:
            headers['HTTP_X_GITHUB_DELIVERY'] = delivery_id
        with mock.patch(
            'community_base.content_sync.webhooks.queue_source_sync',
        ) as mock_queue:
            response = self.client.post(
                '/api/webhooks/github',
                data=body,
                content_type='application/json',
                **headers,
            )
        response.mock_queue = mock_queue
        return response

    # -- first delivery ---------------------------------------------------

    def test_first_delivery_stores_namespaced_key(self):
        response = self._post(self._payload(), delivery_id='delivery-abc')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['message'], 'Sync queued')
        log = PackageWebhookLog.objects.get(service='github')
        self.assertEqual(log.deduplication_key, hashed_key('delivery-abc'))
        self.assertTrue(log.processed)
        self.assertIsNotNone(log.processed_at)

    # -- repeated delivery ------------------------------------------------

    def test_repeated_delivery_is_idempotent_success(self):
        payload = self._payload()
        first = self._post(payload, delivery_id='delivery-rep')
        self.source.refresh_from_db()
        first_webhook_at = self.source.last_webhook_at
        second = self._post(payload, delivery_id='delivery-rep')

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['message'], 'Duplicate delivery')

        self.assertEqual(first.mock_queue.call_count, 1)
        self.assertEqual(PackageWebhookLog.objects.filter(
            service='github',
        ).count(), 1)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_webhook_at, first_webhook_at)

    def test_repeated_delivery_does_not_requeue_when_locked(self):
        """A retry must not queue another engine run either."""
        payload = self._payload()
        self._post(payload, delivery_id='delivery-lock')

        # First delivery queued a sync; simulate it now holding the lock.
        self.source.refresh_from_db()
        self.source.sync_locked_at = timezone_now()
        self.source.save(update_fields=['sync_locked_at', 'updated_at'])

        response = self._post(payload, delivery_id='delivery-lock')

        self.assertEqual(response.json()['message'], 'Duplicate delivery')
        response.mock_queue.assert_not_called()

    def test_repeated_non_push_delivery_creates_one_log(self):
        payload = self._payload(ref='refs/heads/main')
        self._post(payload, event_type='ping', delivery_id='delivery-ping')
        response = self._post(
            payload, event_type='ping', delivery_id='delivery-ping',
        )

        self.assertEqual(response.json()['message'], 'Duplicate delivery')
        self.assertEqual(PackageWebhookLog.objects.filter(
            service='github',
        ).count(), 1)

    def test_replay_after_sync_completed_does_not_requeue(self):
        """A late redelivery long after the sync finished stays a no-op."""
        payload = self._payload()
        self._post(payload, delivery_id='delivery-late')
        self.source.last_sync_status = 'success'
        self.source.save(update_fields=['last_sync_status', 'updated_at'])

        response = self._post(payload, delivery_id='delivery-late')

        self.assertEqual(response.json()['message'], 'Duplicate delivery')
        response.mock_queue.assert_not_called()

    # The forced concurrent-delivery race test was removed with the legacy
    # handler: the package view claims the delivery via get_or_create inside
    # one transaction and does not promise recovery from a lost insert race.

    # -- distinct deliveries ----------------------------------------------

    def test_distinct_delivery_ids_each_enqueue(self):
        first = self._post(self._payload(), delivery_id='delivery-1')
        second = self._post(self._payload(), delivery_id='delivery-2')

        self.assertEqual(first.json()['message'], 'Sync queued')
        self.assertEqual(second.json()['message'], 'Sync queued')
        self.assertEqual(first.mock_queue.call_count, 1)
        self.assertEqual(second.mock_queue.call_count, 1)
        self.assertEqual(PackageWebhookLog.objects.filter(
            service='github',
        ).count(), 2)
        self.assertEqual(
            set(
                PackageWebhookLog.objects.filter(service='github').values_list(
                    'deduplication_key', flat=True,
                )
            ),
            {hashed_key('delivery-1'), hashed_key('delivery-2')},
        )

    def test_same_delivery_id_from_other_service_does_not_block_github(self):
        """The key is namespaced, so another provider cannot squat on it."""
        WebhookLog.objects.create(
            service='calendly',
            event_type='invitee.created',
            deduplication_key='delivery-shared-id',
        )
        response = self._post(
            self._payload(), delivery_id='delivery-shared-id',
        )

        self.assertEqual(response.json()['message'], 'Sync queued')
        self.assertTrue(
            PackageWebhookLog.objects.filter(
                service='github',
                deduplication_key=hashed_key('delivery-shared-id'),
            ).exists()
        )

    # -- no claim without authentication ----------------------------------

    def test_invalid_signature_reserves_no_claim(self):
        payload = self._payload()
        rejected = self._post(
            payload, delivery_id='delivery-x', signature='sha256=bogus',
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertFalse(PackageWebhookLog.objects.exists())
        rejected.mock_queue.assert_not_called()

        # The spoofed attempt must not have burned the delivery id:
        # the real signed delivery still processes.
        accepted = self._post(payload, delivery_id='delivery-x')

        self.assertEqual(accepted.json()['message'], 'Sync queued')
        self.assertEqual(
            PackageWebhookLog.objects.get(service='github').deduplication_key,
            hashed_key('delivery-x'),
        )

    def test_unknown_repository_reserves_no_claim(self):
        body = json.dumps({
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'someone-else/repo'},
        })
        response = self.client.post(
            '/api/webhooks/github',
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=sign(body),
            HTTP_X_GITHUB_EVENT='push',
            HTTP_X_GITHUB_DELIVERY='delivery-unknown-repo',
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PackageWebhookLog.objects.exists())

    def test_unconfigured_secret_reserves_no_claim(self):
        self.source.webhook_secret = ''
        self.source.save(update_fields=['webhook_secret'])
        response = self._post(
            self._payload(), delivery_id='delivery-nosecret',
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(PackageWebhookLog.objects.exists())

    # -- missing / blank delivery header ----------------------------------

    def test_missing_delivery_header_is_rejected_without_claim(self):
        # The legacy handler fell back to a body-derived key; the package
        # webhook requires the delivery id GitHub always sends.
        response = self._post(
            self._payload(), send_delivery_header=False,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PackageWebhookLog.objects.exists())
        response.mock_queue.assert_not_called()

    def test_blank_delivery_header_is_rejected_without_claim(self):
        response = self._post(self._payload(), delivery_id='   ')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PackageWebhookLog.objects.exists())


def timezone_now():
    from django.utils import timezone
    return timezone.now()
