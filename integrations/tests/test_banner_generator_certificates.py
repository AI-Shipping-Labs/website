"""Tests for Banner Generator Buildcamp certificate PDF adapter."""

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from integrations.services.banner_generator import BannerGeneratorError
from integrations.services.banner_generator.certificates import (
    CERTIFICATE_FORMAT,
    CERTIFICATE_SIZE,
    CERTIFICATE_TEMPLATE,
    certificate_s3_key,
    render_certificate_pdf_to_s3,
    render_certificate_preview_pdf,
)


def _fake_course():
    return SimpleNamespace(
        title='AI Engineering Buildcamp',
        slug='ai-buildcamp',
        enroll_url='https://aishippinglabs.com/courses/ai-buildcamp',
    )


def _fake_certificate():
    cert_id = uuid.uuid4()
    user = SimpleNamespace(
        email='grad@example.com',
        get_full_name=lambda: 'Jane Graduate',
    )
    return SimpleNamespace(
        id=cert_id,
        user=user,
        issued_at=datetime(2026, 6, 24, 12, 0, tzinfo=dt_timezone.utc),
        get_absolute_url=lambda: f'/certificates/{cert_id}',
    )


class BannerGeneratorCertificateAdapterTest(SimpleTestCase):
    def test_s3_key_uses_banners_prefix_and_pdf_extension(self):
        cert_id = uuid.uuid4()
        key = certificate_s3_key('ai-buildcamp', cert_id)
        self.assertEqual(key, f'banners/certificates/ai-buildcamp/{cert_id}.pdf')

    @patch('integrations.services.banner_generator.certificates.cdn_url_for_key')
    @patch('integrations.services.banner_generator.certificates.render_to_s3')
    def test_render_to_s3_uses_buildcamp_pdf_template_and_returns_cdn_url(
        self, mock_render_to_s3, mock_cdn_url,
    ):
        cert = _fake_certificate()
        course = _fake_course()
        expected_key = certificate_s3_key(course.slug, cert.id)
        expected_url = f'https://cdn.aishippinglabs.com/{expected_key}'
        mock_cdn_url.return_value = expected_url

        url = render_certificate_pdf_to_s3(cert, course=course)

        self.assertEqual(url, expected_url)
        call_kwargs = mock_render_to_s3.call_args.kwargs
        self.assertEqual(call_kwargs['template'], CERTIFICATE_TEMPLATE)
        self.assertEqual(call_kwargs['size'], CERTIFICATE_SIZE)
        self.assertEqual(call_kwargs['fmt'], CERTIFICATE_FORMAT)
        self.assertEqual(call_kwargs['content_type'], 'application/pdf')
        self.assertEqual(call_kwargs['s3_key'], expected_key)
        self.assertEqual(call_kwargs['data']['name'], 'Jane Graduate')
        self.assertEqual(call_kwargs['data']['certificate_id'], str(cert.id))

    @patch('integrations.services.banner_generator.certificates.cdn_url_for_key')
    @patch('integrations.services.banner_generator.certificates.render_to_s3')
    def test_render_to_s3_retries_once_on_timeout_only(
        self, mock_render_to_s3, mock_cdn_url,
    ):
        cert = _fake_certificate()
        course = _fake_course()
        mock_cdn_url.return_value = 'https://cdn.aishippinglabs.com/banners/certificates/ok.pdf'
        mock_render_to_s3.side_effect = [
            BannerGeneratorError('timeout', is_timeout=True),
            {'ok': True},
        ]

        url = render_certificate_pdf_to_s3(cert, course=course)

        self.assertEqual(url, 'https://cdn.aishippinglabs.com/banners/certificates/ok.pdf')
        self.assertEqual(mock_render_to_s3.call_count, 2)

    @patch('integrations.services.banner_generator.certificates.requests.post')
    @patch('integrations.services.banner_generator.certificates.get_config')
    def test_private_preview_omits_s3_and_returns_pdf_bytes(
        self, mock_config, mock_post,
    ):
        def _cfg(key, default=''):
            values = {
                'BANNER_GENERATOR_FUNCTION_URL': 'https://lambda.example.com/',
                'BANNER_GENERATOR_AUTH_TOKEN': 'secret-token',
                'BANNER_GENERATOR_TIMEOUT_SECONDS': 30,
            }
            return values.get(key, default)

        mock_config.side_effect = _cfg
        response = MagicMock()
        response.status_code = 200
        response.headers = {'Content-Type': 'application/pdf'}
        response.content = b'%PDF-1.4 preview-bytes'
        mock_post.return_value = response

        artifact = render_certificate_preview_pdf(
            recipient_name='Owner Preview',
            course=_fake_course(),
        )

        self.assertEqual(artifact.content, b'%PDF-1.4 preview-bytes')
        self.assertEqual(artifact.content_type, 'application/pdf')
        sent_payload = mock_post.call_args.kwargs['json']
        self.assertNotIn('s3', sent_payload)
        self.assertEqual(sent_payload['template'], CERTIFICATE_TEMPLATE)
        self.assertEqual(sent_payload['format'], 'pdf')

    @patch('integrations.services.banner_generator.certificates.requests.post')
    @patch('integrations.services.banner_generator.certificates.get_config')
    def test_private_preview_timeout_raises_banner_generator_error(
        self, mock_config, mock_post,
    ):
        def _cfg(key, default=''):
            if 'URL' in key:
                return 'https://x.test'
            return 'tok'

        mock_config.side_effect = _cfg
        mock_post.side_effect = requests.Timeout('cold start')

        with self.assertRaises(BannerGeneratorError) as ctx:
            render_certificate_preview_pdf(
                recipient_name='Owner',
                course=_fake_course(),
            )
        self.assertTrue(ctx.exception.is_timeout)
