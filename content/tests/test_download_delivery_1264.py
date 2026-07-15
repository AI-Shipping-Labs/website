import json
from io import StringIO
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.services.privacy import (
    build_user_data_export,
    delete_account_for_privacy,
)
from content.models import Download, DownloadDeliveryGrant
from content.services.download_validation import (
    DownloadMetadataError,
    storage_key_from_configured_s3_url,
    validate_download_metadata,
)
from payments.models import Tier

User = get_user_model()


def make_download(**overrides):
    values = {
        'title': 'AI field guide',
        'slug': 'ai-field-guide',
        'description': 'A practical field guide.',
        'file_url': 'https://downloads-test.s3.eu-central-1.amazonaws.com/downloads/ai-field-guide.pdf',
        'storage_key': 'downloads/ai-field-guide.pdf',
        'file_type': 'pdf',
        'asset_mime_type': 'application/pdf',
        'file_size_bytes': 1024,
        'required_level': 0,
        'published': True,
    }
    values.update(overrides)
    return Download.objects.create(**values)


@override_settings(
    AWS_S3_DOWNLOADS_BUCKET='downloads-test',
    AWS_S3_DOWNLOADS_REGION='eu-central-1',
)
class DownloadDelivery1264Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free_tier, _ = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )
        cls.basic_tier, _ = Tier.objects.get_or_create(
            slug='basic', defaults={'name': 'Basic', 'level': 10},
        )

    def setUp(self):
        cache.clear()
        object_patcher = patch(
            'content.services.download_delivery.verify_download_object_exists',
        )
        self.object_exists = object_patcher.start()
        self.addCleanup(object_patcher.stop)

    def test_catalog_is_clean_and_detail_owns_request_form(self):
        download = make_download()
        catalog = self.client.get('/downloads')
        self.assertContains(
            catalog,
            f'href="{download.get_absolute_url()}?surface=catalog"',
        )
        self.assertNotContains(catalog, 'download-request-form')

        detail = self.client.get(download.get_absolute_url())
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'data-testid="download-request-form"')
        self.assertContains(detail, 'name="newsletter_opt_in"')
        self.assertContains(detail, 'This is optional and requires confirmation.')
        self.assertNotContains(detail, 'https://downloads-test.s3')
        self.assertNotContains(detail, download.storage_key)

    def test_request_rejects_non_object_json_without_server_error(self):
        download = make_download()
        response = self.client.post(
            f'/api/downloads/{download.slug}/request?surface=shortcode',
            data='[]',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid request.'})
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')

    def test_rate_limit_uses_alb_observed_rightmost_forwarded_hop(self):
        from django.test import RequestFactory

        from website.request_ip import client_ip_from_request

        factory = RequestFactory()
        proxied = factory.post(
            '/',
            REMOTE_ADDR='10.0.1.8',
            HTTP_X_FORWARDED_FOR='198.51.100.20',
        )
        attacker_prepended = factory.post(
            '/',
            REMOTE_ADDR='10.0.1.8',
            HTTP_X_FORWARDED_FOR='203.0.113.77, 198.51.100.20',
        )
        multiple_untrusted_hops = factory.post(
            '/',
            REMOTE_ADDR='10.0.1.8',
            HTTP_X_FORWARDED_FOR=(
                '203.0.113.77, 192.0.2.44, 198.51.100.20'
            ),
        )
        spoofed = factory.post(
            '/',
            REMOTE_ADDR='8.8.8.8',
            HTTP_X_FORWARDED_FOR='198.51.100.20',
        )
        malformed = factory.post(
            '/',
            REMOTE_ADDR='10.0.1.8',
            HTTP_X_FORWARDED_FOR='198.51.100.20, not-an-ip',
        )
        self.assertEqual(client_ip_from_request(proxied), '198.51.100.20')
        self.assertEqual(
            client_ip_from_request(attacker_prepended),
            '198.51.100.20',
        )
        self.assertEqual(
            client_ip_from_request(multiple_untrusted_hops),
            '198.51.100.20',
        )
        self.assertEqual(client_ip_from_request(spoofed), '8.8.8.8')
        self.assertEqual(client_ip_from_request(malformed), '10.0.1.8')

    def test_missing_and_unpublished_grant_hits_are_private_404s(self):
        unpublished = make_download(
            slug='unpublished-private-download',
            published=False,
            storage_key='downloads/unpublished-private-download.pdf',
        )
        sentinel_grant = 'SENTINEL_SECRET_GRANT_TOKEN'

        for slug in ('does-not-exist', unpublished.slug):
            with self.subTest(slug=slug):
                response = self.client.get(
                    f'/api/downloads/{slug}/file',
                    {'grant': sentinel_grant},
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response['Cache-Control'],
                    'private, no-store, max-age=0',
                )
                self.assertEqual(response['Pragma'], 'no-cache')
                self.assertEqual(response['Referrer-Policy'], 'no-referrer')
                self.assertNotIn(
                    sentinel_grant,
                    response.content.decode(),
                )
                self.assertNotIn(
                    unpublished.storage_key,
                    response.content.decode(),
                )

    def test_rate_limit_thresholds_and_cache_reset(self):
        from django.test import RequestFactory

        from content.services.download_requests import (
            REQUEST_DOWNLOAD_LIMIT,
            REQUEST_EMAIL_LIMIT,
            REQUEST_IP_LIMIT,
            consume_download_request_rate_limit,
        )

        factory = RequestFactory()
        for index in range(REQUEST_IP_LIMIT):
            request = factory.post('/', REMOTE_ADDR='8.8.8.8')
            self.assertFalse(consume_download_request_rate_limit(
                request,
                f'ip-{index}@example.com',
                f'ip-{index}',
            ))
        self.assertTrue(consume_download_request_rate_limit(
            factory.post('/', REMOTE_ADDR='8.8.8.8'),
            'ip-over@example.com',
            'ip-over',
        ))

        cache.clear()
        for index in range(REQUEST_EMAIL_LIMIT):
            self.assertFalse(consume_download_request_rate_limit(
                factory.post('/', REMOTE_ADDR=f'9.0.0.{index + 1}'),
                'same@example.com',
                f'email-{index}',
            ))
        self.assertTrue(consume_download_request_rate_limit(
            factory.post('/', REMOTE_ADDR='9.0.0.99'),
            'same@example.com',
            'email-over',
        ))

        cache.clear()
        for index in range(REQUEST_DOWNLOAD_LIMIT):
            self.assertFalse(consume_download_request_rate_limit(
                factory.post('/', REMOTE_ADDR=f'11.0.0.{index + 1}'),
                f'slug-{index}@example.com',
                'same-slug',
            ))
        self.assertTrue(consume_download_request_rate_limit(
            factory.post('/', REMOTE_ADDR='11.0.0.99'),
            'slug-over@example.com',
            'same-slug',
        ))

        cache.clear()
        self.assertFalse(consume_download_request_rate_limit(
            factory.post('/', REMOTE_ADDR='8.8.8.8'),
            'after-reset@example.com',
            'after-reset',
        ))

    def test_invalid_and_expired_verification_results_are_never_cached(self):
        from accounts.utils.tokens import generate_user_action_token

        user = User.objects.create_user(email='expired-verify@example.com')
        tokens = [
            'invalid-token',
            generate_user_action_token(
                user.pk,
                'verify_email',
                expiry_hours=-1,
            ),
        ]
        for token in tokens:
            with self.subTest(token=token[:8]):
                response = self.client.get('/api/verify-email', {'token': token})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response['Cache-Control'],
                    'private, no-store, max-age=0',
                )
                self.assertEqual(response['Pragma'], 'no-cache')
                self.assertEqual(response['Referrer-Policy'], 'no-referrer')

    @patch('content.services.download_requests.EmailService.send')
    def test_new_capture_is_download_sourced_and_not_marketing_subscribed(self, send):
        download = make_download()
        response = self.client.post(
            f'/api/downloads/{download.slug}/request?surface=shortcode',
            data=json.dumps({
                'email': 'New-Lead@Example.com',
                'newsletter_opt_in': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        user = User.objects.get(email='new-lead@example.com')
        self.assertEqual(user.signup_source, 'download')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.account_activated)
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_preferences['newsletter'])
        grant = DownloadDeliveryGrant.objects.get(user=user)
        self.assertFalse(grant.newsletter_opt_in)
        self.assertEqual(grant.surface, 'shortcode')
        self.assertIsNone(grant.redeemed_at)
        send.assert_called_once()
        self.assertNotIn(user.email, response.content.decode())

    @patch(
        'content.services.download_requests.EmailService.send',
        side_effect=RuntimeError(
            'SENTINEL_EMAIL=rolled-back@example.com '
            'SENTINEL_ENDPOINT=https://mail-secret.example/send',
        ),
    )
    def test_failed_email_rolls_back_new_capture_and_grant(self, _send):
        download = make_download()
        with self.assertLogs('content.views.api', level='ERROR') as captured:
            response = self.client.post(
                f'/api/downloads/{download.slug}/request',
                data=json.dumps({'email': 'rolled-back@example.com'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(
            User.objects.filter(email='rolled-back@example.com').exists(),
        )
        self.assertFalse(DownloadDeliveryGrant.objects.exists())
        logs = '\n'.join(captured.output)
        self.assertIn('reason=email_delivery_failure', logs)
        self.assertNotIn('SENTINEL_', logs)
        self.assertNotIn('rolled-back@example.com', logs)
        self.assertNotIn('mail-secret.example', logs)
        self.assertNotIn('Traceback', logs)

    @patch('content.services.download_requests.EmailService.send')
    def test_request_response_is_same_for_new_and_existing_address(self, send):
        download = make_download()
        existing = User.objects.create_user(
            email='existing@example.com',
            email_verified=True,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        payloads = []
        for email in ('new@example.com', existing.email):
            response = self.client.post(
                f'/api/downloads/{download.slug}/request',
                data=json.dumps({'email': email}),
                content_type='application/json',
            )
            payloads.append((response.status_code, response.json()))
        self.assertEqual(payloads[0], payloads[1])

    @patch(
        'content.services.download_delivery.build_download_presigned_url',
        return_value='https://signed.example/resource?signature=secret',
    )
    @patch('content.services.download_requests.EmailService.send')
    def test_verified_grant_redeems_once_and_confirms_opt_in(self, send, presign):
        download = make_download()
        user = User.objects.create_user(
            email='verified@example.com',
            email_verified=True,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        response = self.client.post(
            f'/api/downloads/{download.slug}/request',
            data=json.dumps({
                'email': user.email,
                'newsletter_opt_in': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        delivery_url = send.call_args.args[2]['delivery_url']
        send_context = send.call_args.args[2]
        self.assertTrue(send_context['newsletter_opt_in'])
        grant_token = unquote(parse_qs(urlparse(delivery_url).query)['grant'][0])

        first = self.client.get(
            f'/api/downloads/{download.slug}/file',
            {'grant': grant_token},
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first['Location'], 'https://signed.example/resource?signature=secret')
        self.assertEqual(first['Cache-Control'], 'private, no-store, max-age=0')
        download.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(download.download_count, 1)
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_preferences['newsletter'])

        replay = self.client.get(
            f'/api/downloads/{download.slug}/file',
            {'grant': grant_token},
        )
        self.assertEqual(replay.status_code, 403)
        download.refresh_from_db()
        self.assertEqual(download.download_count, 1)
        presign.assert_called_once()

    @patch(
        'content.services.download_delivery.build_download_presigned_url',
        return_value='https://signed.example/unchecked',
    )
    @patch('content.services.download_requests.EmailService.send')
    def test_unchecked_existing_user_preserves_marketing_preferences(
        self,
        send,
        _presign,
    ):
        download = make_download()
        user = User.objects.create_user(
            email='unchecked-existing@example.com',
            email_verified=True,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        self.client.post(
            f'/api/downloads/{download.slug}/request',
            data=json.dumps({'email': user.email, 'newsletter_opt_in': False}),
            content_type='application/json',
        )
        context = send.call_args.args[2]
        self.assertFalse(context['newsletter_opt_in'])
        grant_token = unquote(
            parse_qs(urlparse(context['delivery_url']).query)['grant'][0],
        )
        response = self.client.get(
            f'/api/downloads/{download.slug}/file',
            {'grant': grant_token},
        )
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_preferences['newsletter'])

    @patch('content.services.download_requests.EmailService.send')
    def test_third_party_checked_request_requires_mailbox_click_to_subscribe(
        self,
        send,
    ):
        download = make_download()
        victim = User.objects.create_user(
            email='victim@example.com',
            email_verified=True,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        response = self.client.post(
            f'/api/downloads/{download.slug}/request',
            data=json.dumps({
                'email': victim.email,
                'newsletter_opt_in': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 202)
        victim.refresh_from_db()
        self.assertTrue(victim.unsubscribed)
        self.assertFalse(victim.email_preferences['newsletter'])
        self.assertTrue(send.call_args.args[2]['newsletter_opt_in'])

    @patch(
        'content.services.download_delivery.build_download_presigned_url',
        return_value='https://signed.example/resource',
    )
    @patch('content.services.download_requests.EmailService.send')
    def test_unverified_mailbox_verification_continues_to_grant(self, send, _presign):
        download = make_download()
        self.client.post(
            f'/api/downloads/{download.slug}/request',
            data=json.dumps({'email': 'verify-download@example.com'}),
            content_type='application/json',
        )
        delivery_url = send.call_args.args[2]['delivery_url']
        response = self.client.get(urlparse(delivery_url).path + '?' + urlparse(delivery_url).query)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')
        self.assertEqual(response['Pragma'], 'no-cache')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        grant_response = self.client.get(response['Location'])
        self.assertEqual(grant_response.status_code, 302)
        user = User.objects.get(email='verify-download@example.com')
        self.assertTrue(user.email_verified)
        self.assertFalse(user.account_activated)

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_under_tier_session_is_denied_before_presign(self, presign):
        download = make_download(required_level=10)
        user = User.objects.create_user(
            email='free@example.com', password='password',
            email_verified=True, tier=self.free_tier,
        )
        self.client.force_login(user)
        response = self.client.get(f'/api/downloads/{download.slug}/file')
        self.assertEqual(response.status_code, 403)
        presign.assert_not_called()
        download.refresh_from_db()
        self.assertEqual(download.download_count, 0)

    @patch('content.services.download_delivery.build_download_presigned_url')
    @patch('content.services.download_requests.EmailService.send')
    def test_under_tier_grant_redirects_to_safe_recovery(self, send, presign):
        download = make_download(required_level=30)
        user = User.objects.create_user(
            email='basic-requester@example.com', password='password',
            email_verified=True, tier=self.basic_tier,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        request_response = self.client.post(
            f'/api/downloads/{download.slug}/request',
            data=json.dumps({
                'email': user.email,
                'newsletter_opt_in': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(request_response.status_code, 202)
        delivery_url = send.call_args.args[2]['delivery_url']
        redemption = self.client.get(
            urlparse(delivery_url).path + '?' + urlparse(delivery_url).query,
        )
        self.assertRedirects(
            redemption,
            f'{download.get_absolute_url()}?delivery=access-required',
        )
        recovery = self.client.get(redemption['Location'])
        self.assertContains(
            recovery,
            'data-testid="download-access-required-notice"',
        )
        self.assertContains(recovery, 'data-testid="download-pricing-cta"')
        self.assertContains(recovery, 'Already a member? Sign in')
        self.assertNotContains(recovery, user.email)
        self.assertEqual(
            redemption['Cache-Control'],
            'private, no-store, max-age=0',
        )
        presign.assert_not_called()
        download.refresh_from_db()
        self.assertEqual(download.download_count, 0)
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_preferences['newsletter'])

    @patch(
        'content.services.download_delivery.build_download_presigned_url',
        return_value='https://signed.example/direct',
    )
    def test_eligible_authenticated_member_gets_direct_presigned_handoff(self, presign):
        download = make_download(required_level=10)
        user = User.objects.create_user(
            email='basic@example.com', password='password',
            email_verified=True, tier=self.basic_tier,
        )
        self.client.force_login(user)
        detail = self.client.get(download.get_absolute_url())
        self.assertContains(detail, 'data-testid="download-file-cta"')
        response = self.client.get(f'/api/downloads/{download.slug}/file')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://signed.example/direct')
        presign.assert_called_once_with(download)

    @patch(
        'content.services.download_delivery.build_download_presigned_url',
        return_value='https://signed.example/activity',
    )
    def test_success_records_activity_and_coarse_surface_analytics(self, _presign):
        from analytics.models import UserActivity

        download = make_download(required_level=10)
        user = User.objects.create_user(
            email='analytics-private@example.com',
            password='password',
            email_verified=True,
            tier=self.basic_tier,
        )
        self.client.force_login(user)

        with self.assertLogs('content.views.api', level='INFO') as captured:
            response = self.client.get(
                f'/api/downloads/{download.slug}/file?surface=catalog',
            )

        self.assertEqual(response.status_code, 302)
        activity = UserActivity.objects.get(
            user=user,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
        )
        self.assertEqual(activity.object_type, 'download')
        self.assertEqual(activity.object_id, download.slug)
        self.assertEqual(activity.target_url, download.get_absolute_url())
        logs = '\n'.join(captured.output)
        self.assertIn(
            'download_delivery_succeeded slug=ai-field-guide '
            'required_level=10 surface=catalog',
            logs,
        )
        self.assertNotIn(user.email, logs)

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_object_deleted_after_sync_fails_before_count_or_success(self, presign):
        from content.services.download_delivery import create_delivery_grant

        download = make_download()
        user = User.objects.create_user(
            email='missing-object@example.com',
            email_verified=True,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        grant_token = create_delivery_grant(
            user,
            download,
            newsletter_opt_in=True,
        )
        self.object_exists.side_effect = ValueError(
            'SENTINEL_KEY=downloads/private-secret.pdf '
            'SENTINEL_ENDPOINT=https://s3-secret.example/object',
        )

        with self.assertLogs('content.views.api', level='INFO') as captured:
            response = self.client.get(
                f'/api/downloads/{download.slug}/file',
                {'grant': grant_token},
            )

        self.assertEqual(response.status_code, 503)
        download.refresh_from_db()
        grant = DownloadDeliveryGrant.objects.get(download=download, user=user)
        self.assertEqual(download.download_count, 0)
        self.assertIsNone(grant.redeemed_at)
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_preferences['newsletter'])
        presign.assert_not_called()
        logs = '\n'.join(captured.output)
        self.assertIn(
            'download_delivery_denied slug=ai-field-guide '
            'required_level=0 surface=detail '
            'reason=grant_presign_failure',
            logs,
        )
        self.assertNotIn('download_delivery_succeeded', logs)
        self.assertNotIn('SENTINEL_', logs)
        self.assertNotIn('private-secret.pdf', logs)
        self.assertNotIn('s3-secret.example', logs)
        self.assertNotIn('Traceback', logs)

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_presign_exception_does_not_consume_grant_or_count(self, presign):
        from content.services.download_delivery import create_delivery_grant

        presign.side_effect = RuntimeError(
            'SENTINEL_TOKEN=secret-grant-token '
            'SENTINEL_ENDPOINT=https://presign-secret.example',
        )
        download = make_download()
        user = User.objects.create_user(
            email='presign-failure@example.com',
            email_verified=True,
            unsubscribed=True,
            email_preferences={'newsletter': False},
        )
        token = create_delivery_grant(
            user,
            download,
            newsletter_opt_in=True,
        )
        with self.assertLogs('content.views.api', level='ERROR') as captured:
            response = self.client.get(
                f'/api/downloads/{download.slug}/file',
                {'grant': token},
            )
        self.assertEqual(response.status_code, 503)
        download.refresh_from_db()
        grant = DownloadDeliveryGrant.objects.get(user=user, download=download)
        self.assertEqual(download.download_count, 0)
        self.assertIsNone(grant.redeemed_at)
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_preferences['newsletter'])
        logs = '\n'.join(captured.output)
        self.assertIn(
            'download_delivery_denied slug=ai-field-guide '
            'required_level=0 surface=detail '
            'reason=grant_presign_failure',
            logs,
        )
        self.assertNotIn('SENTINEL_', logs)
        self.assertNotIn('secret-grant-token', logs)
        self.assertNotIn('presign-secret.example', logs)
        self.assertNotIn('Traceback', logs)

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_session_presign_error_log_omits_provider_payload(self, presign):
        presign.side_effect = RuntimeError(
            'SENTINEL_KEY=downloads/session-secret.pdf '
            'SENTINEL_TOKEN=session-secret-token '
            'SENTINEL_ENDPOINT=https://session-presign-secret.example',
        )
        download = make_download(required_level=10)
        user = User.objects.create_user(
            email='session-secret@example.com',
            password='password',
            email_verified=True,
            tier=self.basic_tier,
        )
        self.client.force_login(user)

        with self.assertLogs('content.views.api', level='ERROR') as captured:
            response = self.client.get(f'/api/downloads/{download.slug}/file')

        self.assertEqual(response.status_code, 503)
        download.refresh_from_db()
        self.assertEqual(download.download_count, 0)
        logs = '\n'.join(captured.output)
        self.assertIn(
            'download_delivery_denied slug=ai-field-guide '
            'required_level=10 surface=detail '
            'reason=session_presign_failure',
            logs,
        )
        self.assertNotIn('SENTINEL_', logs)
        self.assertNotIn('session-secret', logs)
        self.assertNotIn('Traceback', logs)

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_grant_tamper_expiry_and_slug_rebinding_fail_closed(self, presign):
        import datetime

        from content.services.download_delivery import create_delivery_grant

        download = make_download()
        other = make_download(slug='other-guide')
        user = User.objects.create_user(
            email='grant-safety@example.com',
            email_verified=True,
        )

        token = create_delivery_grant(user, download)
        tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')
        response = self.client.get(
            f'/api/downloads/{download.slug}/file',
            {'grant': tampered},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.get(
            f'/api/downloads/{other.slug}/file',
            {'grant': token},
        )
        self.assertEqual(response.status_code, 403)

        grant = DownloadDeliveryGrant.objects.get(user=user, download=download)
        grant.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        grant.save(update_fields=['expires_at'])
        response = self.client.get(
            f'/api/downloads/{download.slug}/file',
            {'grant': token},
        )
        self.assertEqual(response.status_code, 403)
        download.refresh_from_db()
        self.assertEqual(download.download_count, 0)
        presign.assert_not_called()

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_publication_change_invalidates_unredeemed_grant(self, presign):
        from content.services.download_delivery import create_delivery_grant

        download = make_download()
        user = User.objects.create_user(
            email='unpublished-grant@example.com',
            email_verified=True,
        )
        token = create_delivery_grant(user, download)
        download.published = False
        download.save(update_fields=['published'])

        response = self.client.get(
            f'/api/downloads/{download.slug}/file',
            {'grant': token},
        )
        self.assertEqual(response.status_code, 404)
        grant = DownloadDeliveryGrant.objects.get(user=user, download=download)
        self.assertIsNone(grant.redeemed_at)
        presign.assert_not_called()

    @patch('content.services.download_delivery.build_download_presigned_url')
    def test_unready_asset_fails_closed(self, presign):
        download = make_download(storage_key='')
        user = User.objects.create_user(
            email='ready@example.com', password='password', email_verified=True,
        )
        self.client.force_login(user)
        response = self.client.get(f'/api/downloads/{download.slug}/file')
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, download.file_url, status_code=503)
        presign.assert_not_called()

    def test_privacy_export_includes_grant_without_secret_hash(self):
        download = make_download()
        user = User.objects.create_user(email='privacy@example.com')
        from content.services.download_delivery import create_delivery_grant
        create_delivery_grant(user, download, surface='catalog')
        export = build_user_data_export(user)
        grants = export['learning_content']['download_delivery_grants']
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]['surface'], 'catalog')
        self.assertNotIn('token_hash', grants[0])

    @patch('accounts.services.privacy.notify_privacy_staff')
    def test_privacy_deletion_cascades_grant_and_reports_it(self, _notify):
        download = make_download()
        user = User.objects.create_user(email='privacy-delete@example.com')
        from content.services.download_delivery import create_delivery_grant
        create_delivery_grant(user, download)

        result = delete_account_for_privacy(user)

        self.assertTrue(result.success)
        self.assertFalse(DownloadDeliveryGrant.objects.exists())
        self.assertEqual(
            result.row_count_summary['erased']['content.DownloadDeliveryGrant'],
            1,
        )


class DownloadValidationAndBackfill1264Test(TestCase):
    def test_slides_use_extension_specific_mime_and_filename(self):
        legacy = validate_download_metadata(
            storage_key='downloads/talk.ppt',
            file_type='slides',
            file_size_bytes=10,
            required_level=0,
        )
        modern = validate_download_metadata(
            storage_key='downloads/talk.pptx',
            file_type='slides',
            file_size_bytes=10,
            required_level=0,
        )
        self.assertEqual(
            legacy['asset_mime_type'],
            'application/vnd.ms-powerpoint',
        )
        self.assertEqual(
            modern['asset_mime_type'],
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        )
        download = make_download(
            slug='legacy-talk',
            storage_key='downloads/talk.ppt',
            file_type='slides',
            asset_mime_type=legacy['asset_mime_type'],
        )
        self.assertEqual(download.safe_filename, 'legacy-talk.ppt')
        self.assertEqual(download.resolved_mime_type, legacy['asset_mime_type'])

    def test_other_type_is_preserved_but_not_deliverable(self):
        download = make_download(
            slug='legacy-other',
            storage_key='downloads/archive.bin',
            file_type='other',
            asset_mime_type='application/octet-stream',
        )
        self.assertFalse(download.delivery_ready)
        with self.assertRaises(DownloadMetadataError):
            validate_download_metadata(
                storage_key=download.storage_key,
                file_type='other',
                file_size_bytes=10,
                required_level=0,
                asset_mime_type=download.asset_mime_type,
            )

    def test_validation_rejects_encoded_traversal_and_type_mismatch(self):
        with self.assertRaises(DownloadMetadataError):
            validate_download_metadata(
                storage_key='downloads/%2e%2e/secret.pdf',
                file_type='pdf',
                file_size_bytes=10,
                required_level=0,
            )
        with self.assertRaises(DownloadMetadataError):
            validate_download_metadata(
                storage_key='downloads/unsafe.exe',
                file_type='other',
                file_size_bytes=10,
                required_level=0,
            )
        with self.assertRaises(DownloadMetadataError):
            validate_download_metadata(
                storage_key='downloads/guide.pdf',
                file_type='pdf',
                file_size_bytes=10,
                required_level=999,
            )
        with self.assertRaises(DownloadMetadataError):
            validate_download_metadata(
                storage_key='downloads/%25252e%25252e/secret.pdf',
                file_type='pdf',
                file_size_bytes=10,
                required_level=0,
            )
        with self.assertRaises(DownloadMetadataError):
            validate_download_metadata(
                storage_key='downloads/guide.zip',
                file_type='pdf',
                file_size_bytes=10,
                required_level=0,
            )

    def test_s3_url_parser_accepts_only_configured_bucket(self):
        key = storage_key_from_configured_s3_url(
            'https://private.s3.eu-central-1.amazonaws.com/downloads/guide.pdf',
            'private',
            'eu-central-1',
        )
        self.assertEqual(key, 'downloads/guide.pdf')
        with self.assertRaises(DownloadMetadataError):
            storage_key_from_configured_s3_url(
                'https://other.s3.eu-central-1.amazonaws.com/downloads/guide.pdf',
                'private',
                'eu-central-1',
            )
        with self.assertRaises(DownloadMetadataError):
            storage_key_from_configured_s3_url(
                'https://private.s3.eu-central-1.amazonaws.com//downloads/guide.pdf',
                'private',
                'eu-central-1',
            )
        for malformed in (
            'https://[broken/downloads/guide.pdf',
            'https://private.s3.eu-central-1.amazonaws.com:not-a-port/downloads/guide.pdf',
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaisesMessage(
                    DownloadMetadataError,
                    'URL is malformed',
                ):
                    storage_key_from_configured_s3_url(
                        malformed,
                        'private',
                        'eu-central-1',
                    )

    @patch(
        'content.management.commands.backfill_download_storage_keys.get_downloads_s3_config',
        return_value={'bucket': '', 'region': 'eu-central-1'},
    )
    def test_backfill_fails_nonzero_when_bucket_is_unconfigured(self, _config):
        make_download(storage_key='')
        with self.assertRaisesMessage(
            CommandError,
            'AWS_S3_DOWNLOADS_BUCKET is not configured',
        ):
            call_command('backfill_download_storage_keys')

    @override_settings(
        AWS_S3_DOWNLOADS_BUCKET='private',
        AWS_S3_DOWNLOADS_REGION='eu-central-1',
    )
    def test_backfill_is_dry_run_then_idempotent_apply(self):
        download = make_download(
            file_url='https://private.s3.eu-central-1.amazonaws.com/downloads/ai-field-guide.pdf',
            storage_key='',
        )
        out = StringIO()
        call_command('backfill_download_storage_keys', stdout=out)
        download.refresh_from_db()
        self.assertEqual(download.storage_key, '')
        self.assertIn('DRY RUN', out.getvalue())

        call_command('backfill_download_storage_keys', '--apply', stdout=StringIO())
        download.refresh_from_db()
        self.assertEqual(download.storage_key, 'downloads/ai-field-guide.pdf')
        second = StringIO()
        call_command('backfill_download_storage_keys', '--apply', stdout=second)
        self.assertIn('ready=1 mappable=0', second.getvalue())

    @override_settings(
        AWS_S3_DOWNLOADS_BUCKET='private',
        AWS_S3_DOWNLOADS_REGION='eu-central-1',
    )
    def test_backfill_preserves_unresolved_external_legacy_row(self):
        download = make_download(
            file_url='https://external.example/files/legacy.pdf',
            storage_key='',
            download_count=17,
            published=True,
        )
        before = {
            'file_url': download.file_url,
            'storage_key': download.storage_key,
            'download_count': download.download_count,
            'published': download.published,
        }

        output = StringIO()
        call_command(
            'backfill_download_storage_keys',
            '--apply',
            stdout=output,
        )
        download.refresh_from_db()

        self.assertIn(f'UNRESOLVED {download.slug}', output.getvalue())
        self.assertEqual(
            {
                'file_url': download.file_url,
                'storage_key': download.storage_key,
                'download_count': download.download_count,
                'published': download.published,
            },
            before,
        )

    @override_settings(
        AWS_S3_DOWNLOADS_BUCKET='private',
        AWS_S3_DOWNLOADS_REGION='eu-central-1',
    )
    def test_backfill_dry_run_continues_after_malformed_bracket_host(self):
        malformed = make_download(
            slug='malformed-bracket-host',
            file_url='https://[broken/downloads/malformed.pdf',
            storage_key='',
        )
        valid = make_download(
            slug='valid-after-malformed-dry',
            file_url=(
                'https://private.s3.eu-central-1.amazonaws.com/'
                'downloads/valid-after-malformed-dry.pdf'
            ),
            storage_key='',
        )

        output = StringIO()
        call_command('backfill_download_storage_keys', stdout=output)

        malformed.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(malformed.storage_key, '')
        self.assertEqual(valid.storage_key, '')
        text = output.getvalue()
        self.assertIn('UNRESOLVED malformed-bracket-host: URL is malformed', text)
        self.assertIn('MAPPABLE valid-after-malformed-dry', text)
        self.assertIn('DRY RUN: ready=0 mappable=1 unresolved=1', text)

    @override_settings(
        AWS_S3_DOWNLOADS_BUCKET='private',
        AWS_S3_DOWNLOADS_REGION='eu-central-1',
    )
    def test_backfill_apply_continues_after_malformed_port(self):
        malformed = make_download(
            slug='malformed-port',
            file_url=(
                'https://private.s3.eu-central-1.amazonaws.com:not-a-port/'
                'downloads/malformed.pdf'
            ),
            storage_key='',
        )
        valid = make_download(
            slug='valid-after-malformed-apply',
            file_url=(
                'https://private.s3.eu-central-1.amazonaws.com/'
                'downloads/valid-after-malformed-apply.pdf'
            ),
            storage_key='',
        )

        output = StringIO()
        call_command(
            'backfill_download_storage_keys',
            '--apply',
            stdout=output,
        )

        malformed.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(malformed.storage_key, '')
        self.assertEqual(
            valid.storage_key,
            'downloads/valid-after-malformed-apply.pdf',
        )
        text = output.getvalue()
        self.assertIn('UNRESOLVED malformed-port: URL is malformed', text)
        self.assertIn('MAPPABLE valid-after-malformed-apply', text)
        self.assertIn('APPLY: ready=0 mappable=1 unresolved=1', text)
