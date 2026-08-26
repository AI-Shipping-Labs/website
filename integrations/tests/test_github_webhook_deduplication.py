"""GitHub webhook delivery deduplication (issue #1433).

GitHub retries a delivery (and operators can redeliver one by hand) with
the same ``X-GitHub-Delivery`` id. Without a claim on that id, every
retry re-queued a content sync, wrote another queued ``SyncLog``, and
rewrote ``ContentSource`` state. These tests pin the claim contract on
``WebhookLog.deduplication_key``.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone

from integrations.models import ContentSource, SyncLog, WebhookLog
from integrations.services.github import delivery_deduplication_key

WEBHOOK_SECRET = 'dedup-github-webhook-secret'
REPO = 'AI-Shipping-Labs/dedup-content'


def sign(body, secret=WEBHOOK_SECRET):
    """Build a valid ``X-Hub-Signature-256`` header for ``body``."""
    if isinstance(body, str):
        body = body.encode('utf-8')
    digest = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f'sha256={digest}'


@tag('core')
class GitHubWebhookDeduplicationTest(TestCase):
    """A repeated GitHub delivery must be an idempotent no-op."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name=REPO,
            webhook_secret=WEBHOOK_SECRET,
        )

    def _payload(self, ref='refs/heads/main', **extra):
        payload = {'ref': ref, 'repository': {'full_name': REPO}}
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
        return self.client.post(
            '/api/webhooks/github',
            data=body,
            content_type='application/json',
            **headers,
        )

    # -- first delivery ---------------------------------------------------

    def test_first_delivery_stores_namespaced_key(self):
        with patch('django_q.tasks.async_task', return_value='task-1'):
            response = self._post(self._payload(), delivery_id='delivery-abc')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        log = WebhookLog.objects.get(service='github')
        self.assertEqual(log.deduplication_key, 'github:delivery:delivery-abc')
        self.assertTrue(log.processed)
        self.assertIsNotNone(log.processed_at)

    # -- repeated delivery ------------------------------------------------

    def test_repeated_delivery_is_idempotent_success(self):
        payload = self._payload()
        with patch('django_q.tasks.async_task', return_value='task-1') as mock_async:
            first = self._post(payload)
            self.source.refresh_from_db()
            first_webhook_at = self.source.last_webhook_at
            second = self._post(payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['status'], 'ok')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['status'], 'duplicate')

        mock_async.assert_called_once()
        self.assertEqual(WebhookLog.objects.filter(service='github').count(), 1)
        self.assertEqual(SyncLog.objects.filter(source=self.source).count(), 1)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_webhook_at, first_webhook_at)
        self.assertFalse(self.source.sync_requested)

    def test_repeated_delivery_does_not_set_sync_requested_when_locked(self):
        """A retry must not re-arm the follow-up sync flag either."""
        payload = self._payload()
        with patch('django_q.tasks.async_task', return_value='task-1'):
            self._post(payload)

        # First delivery queued a sync; simulate it now holding the lock.
        self.source.refresh_from_db()
        self.source.sync_locked_at = timezone.now()
        self.source.save(update_fields=['sync_locked_at', 'updated_at'])

        with patch('django_q.tasks.async_task') as mock_async:
            response = self._post(payload)

        self.assertEqual(response.json()['status'], 'duplicate')
        mock_async.assert_not_called()
        self.source.refresh_from_db()
        self.assertFalse(self.source.sync_requested)

    def test_repeated_non_push_delivery_creates_one_log(self):
        payload = self._payload(ref='refs/heads/main')
        self._post(payload, event_type='ping')
        response = self._post(payload, event_type='ping')

        self.assertEqual(response.json()['status'], 'duplicate')
        self.assertEqual(WebhookLog.objects.filter(service='github').count(), 1)

    def test_replay_after_sync_completed_does_not_requeue(self):
        """A late redelivery long after the sync finished stays a no-op."""
        payload = self._payload()
        with patch('django_q.tasks.async_task', return_value='task-1'):
            self._post(payload)
        SyncLog.objects.filter(source=self.source).update(status='success')
        self.source.last_sync_status = 'success'
        self.source.save(update_fields=['last_sync_status', 'updated_at'])

        with (
            patch('django_q.tasks.async_task') as mock_async,
            patch(
                'integrations.views.github_webhook.sync_content_source',
            ) as mock_sync,
        ):
            response = self._post(payload)

        self.assertEqual(response.json()['status'], 'duplicate')
        mock_async.assert_not_called()
        mock_sync.assert_not_called()
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'success')
        self.assertEqual(
            SyncLog.objects.filter(source=self.source, status='queued').count(),
            0,
        )

    # -- concurrency ------------------------------------------------------

    def test_concurrent_duplicate_converges_without_integrity_error(self):
        """Two racing deliveries: one processes, the other sees a duplicate.

        The race is reproduced deterministically by making the second
        request's initial ``WebhookLog`` lookup miss the row the winner
        already committed, which is exactly what a concurrent request sees
        when both read before either writes. The unique constraint then
        rejects the losing insert and the handler must recover from it.
        """
        payload = self._payload()
        with patch('django_q.tasks.async_task', return_value='task-1') as mock_async:
            self._post(payload)

            original_get = QuerySet.get
            state = {'missed': False}

            def racing_get(self, *args, **kwargs):
                if self.model is WebhookLog and not state['missed']:
                    state['missed'] = True
                    raise WebhookLog.DoesNotExist
                return original_get(self, *args, **kwargs)

            with patch.object(QuerySet, 'get', racing_get):
                response = self._post(payload)

        self.assertTrue(state['missed'], 'race path was not exercised')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'duplicate')
        mock_async.assert_called_once()
        self.assertEqual(WebhookLog.objects.filter(service='github').count(), 1)
        self.assertEqual(SyncLog.objects.filter(source=self.source).count(), 1)

    # -- distinct deliveries ----------------------------------------------

    def test_distinct_delivery_ids_each_enqueue(self):
        with patch('django_q.tasks.async_task', return_value='task') as mock_async:
            first = self._post(self._payload(), delivery_id='delivery-1')
            second = self._post(self._payload(), delivery_id='delivery-2')

        self.assertEqual(first.json()['status'], 'ok')
        self.assertEqual(second.json()['status'], 'ok')
        self.assertEqual(mock_async.call_count, 2)
        self.assertEqual(WebhookLog.objects.filter(service='github').count(), 2)
        self.assertEqual(
            SyncLog.objects.filter(source=self.source, status='queued').count(),
            2,
        )
        self.assertEqual(
            set(
                WebhookLog.objects.filter(service='github').values_list(
                    'deduplication_key', flat=True,
                )
            ),
            {'github:delivery:delivery-1', 'github:delivery:delivery-2'},
        )

    def test_same_delivery_id_from_other_service_does_not_block_github(self):
        """The key is namespaced, so another provider cannot squat on it."""
        WebhookLog.objects.create(
            service='calendly',
            event_type='invitee.created',
            deduplication_key='delivery-shared-id',
        )
        with patch('django_q.tasks.async_task', return_value='task'):
            response = self._post(
                self._payload(), delivery_id='delivery-shared-id',
            )

        self.assertEqual(response.json()['status'], 'ok')
        self.assertTrue(
            WebhookLog.objects.filter(
                service='github',
                deduplication_key='github:delivery:delivery-shared-id',
            ).exists()
        )

    # -- no claim without authentication ----------------------------------

    def test_invalid_signature_reserves_no_claim(self):
        payload = self._payload()
        with patch('django_q.tasks.async_task') as mock_async:
            rejected = self._post(
                payload, delivery_id='delivery-x', signature='sha256=bogus',
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertFalse(WebhookLog.objects.exists())
            mock_async.assert_not_called()

            # The spoofed attempt must not have burned the delivery id:
            # the real signed delivery still processes.
            accepted = self._post(payload, delivery_id='delivery-x')

        self.assertEqual(accepted.json()['status'], 'ok')
        mock_async.assert_called_once()
        self.assertEqual(
            WebhookLog.objects.get(service='github').deduplication_key,
            'github:delivery:delivery-x',
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
        self.assertFalse(WebhookLog.objects.exists())

    def test_unconfigured_secret_reserves_no_claim(self):
        self.source.webhook_secret = ''
        self.source.save(update_fields=['webhook_secret'])
        response = self._post(self._payload(), delivery_id='delivery-nosecret')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WebhookLog.objects.exists())

    # -- missing / blank delivery header ----------------------------------

    def test_missing_delivery_header_falls_back_to_signed_body_key(self):
        payload = self._payload()
        with patch('django_q.tasks.async_task', return_value='task') as mock_async:
            first = self._post(payload, send_delivery_header=False)
            second = self._post(payload, send_delivery_header=False)

        self.assertEqual(first.json()['status'], 'ok')
        self.assertEqual(second.json()['status'], 'duplicate')
        mock_async.assert_called_once()
        self.assertEqual(WebhookLog.objects.filter(service='github').count(), 1)
        log = WebhookLog.objects.get(service='github')
        self.assertTrue(log.deduplication_key.startswith('github:body:'))

    def test_blank_delivery_header_falls_back_to_signed_body_key(self):
        payload = self._payload()
        with patch('django_q.tasks.async_task', return_value='task') as mock_async:
            first = self._post(payload, delivery_id='   ')
            second = self._post(payload, delivery_id='')

        self.assertEqual(first.json()['status'], 'ok')
        self.assertEqual(second.json()['status'], 'duplicate')
        mock_async.assert_called_once()
        self.assertTrue(
            WebhookLog.objects.get(
                service='github',
            ).deduplication_key.startswith('github:body:')
        )

    def test_missing_delivery_header_still_allows_distinct_pushes(self):
        with patch('django_q.tasks.async_task', return_value='task') as mock_async:
            first = self._post(
                self._payload(after='commit-one'), send_delivery_header=False,
            )
            second = self._post(
                self._payload(after='commit-two'), send_delivery_header=False,
            )

        self.assertEqual(first.json()['status'], 'ok')
        self.assertEqual(second.json()['status'], 'ok')
        self.assertEqual(mock_async.call_count, 2)
        self.assertEqual(
            SyncLog.objects.filter(source=self.source, status='queued').count(),
            2,
        )

    # -- failure releases the claim ---------------------------------------

    def test_failed_processing_releases_claim_for_redelivery(self):
        payload = self._payload()
        with patch(
            'django_q.tasks.async_task', side_effect=RuntimeError('queue down'),
        ):
            failed = self._post(payload, delivery_id='delivery-retry')

        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()['status'], 'error')
        failed_log = WebhookLog.objects.get(service='github')
        self.assertIsNone(failed_log.deduplication_key)
        self.assertFalse(failed_log.processed)
        self.assertEqual(failed_log.attempts, 1)
        self.assertIn('queue down', failed_log.error_message)

        with patch('django_q.tasks.async_task', return_value='task') as mock_async:
            retried = self._post(payload, delivery_id='delivery-retry')

        self.assertEqual(retried.json()['status'], 'ok')
        mock_async.assert_called_once()
        self.assertTrue(
            WebhookLog.objects.filter(
                deduplication_key='github:delivery:delivery-retry',
                processed=True,
            ).exists()
        )


@tag('core')
class GitHubDeliveryKeyTest(TestCase):
    """Key construction rules for ``delivery_deduplication_key``."""

    def _request(self, delivery_id=None, body=b'{}', signature='sha256=abc'):
        headers = {'HTTP_X_HUB_SIGNATURE_256': signature}
        if delivery_id is not None:
            headers['HTTP_X_GITHUB_DELIVERY'] = delivery_id
        return RequestFactory().post(
            '/api/webhooks/github',
            data=body,
            content_type='application/json',
            **headers,
        )

    def test_uuid_delivery_id_is_kept_readable(self):
        key = delivery_deduplication_key(
            self._request('72d3162e-cc78-11e3-81ab-4c9367dc0958'),
        )
        self.assertEqual(
            key, 'github:delivery:72d3162e-cc78-11e3-81ab-4c9367dc0958',
        )

    def test_oversized_delivery_id_is_hashed_within_column_limit(self):
        oversized = 'x' * 500
        key = delivery_deduplication_key(self._request(oversized))
        expected = hashlib.sha256(oversized.encode('utf-8')).hexdigest()
        self.assertEqual(key, f'github:delivery:sha256:{expected}')
        self.assertLessEqual(len(key), 128)

    def test_oversized_delivery_ids_stay_distinct(self):
        first = delivery_deduplication_key(self._request('a' * 500))
        second = delivery_deduplication_key(self._request('b' * 500))
        self.assertNotEqual(first, second)

    def test_delivery_id_with_separators_is_hashed(self):
        """A colon-bearing id must not forge another namespace's key."""
        key = delivery_deduplication_key(self._request('github:body:spoof'))
        self.assertTrue(key.startswith('github:delivery:sha256:'))

    def test_fallback_key_depends_on_signature_and_body(self):
        body = b'{"ref": "refs/heads/main"}'
        key = delivery_deduplication_key(
            self._request(body=body, signature='sha256=aaa'),
        )
        other_signature = delivery_deduplication_key(
            self._request(body=body, signature='sha256=bbb'),
        )
        other_body = delivery_deduplication_key(
            self._request(body=b'{"ref": "refs/heads/master"}',
                          signature='sha256=aaa'),
        )
        self.assertTrue(key.startswith('github:body:'))
        self.assertNotEqual(key, other_signature)
        self.assertNotEqual(key, other_body)
        self.assertLessEqual(len(key), 128)
