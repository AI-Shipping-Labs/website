"""Tests for production post-deploy sitemap verification (issue #1377)."""

import importlib.util
import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, tag


def _load_verify_sitemap_module():
    module_path = Path(__file__).resolve().parent.parent / 'deploy' / 'verify_sitemap.py'
    spec = importlib.util.spec_from_file_location('deploy_verify_sitemap', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_sitemap = _load_verify_sitemap_module()


def _xml(*locations):
    entries = ''.join(f'<url><loc>{location}</loc></url>' for location in locations)
    return f'<?xml version="1.0"?><urlset>{entries}</urlset>'.encode()


@tag('core')
class SitemapValidationTest(SimpleTestCase):
    def test_accepts_nonempty_canonical_sitemap(self):
        count = verify_sitemap.validate_sitemap(
            _xml(
                'https://aishippinglabs.com/',
                'https://aishippinglabs.com/blog/example',
            ),
            'https://aishippinglabs.com',
        )
        self.assertEqual(count, 2)

    def test_rejects_empty_and_malformed_sitemaps(self):
        cases = (
            (b'<urlset></urlset>', 'empty or blank'),
            (b'<urlset><url>', 'malformed XML'),
        )
        for body, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    verify_sitemap.SitemapVerificationError,
                    message,
                ):
                    verify_sitemap.validate_sitemap(
                        body,
                        'https://aishippinglabs.com',
                    )

    def test_rejects_relative_alias_dev_example_and_mixed_origins(self):
        bad_locations = (
            '/relative',
            'https://www.aishippinglabs.com/alias',
            'https://dev.aishippinglabs.com/dev',
            'https://example.com/stale',
            'http://aishippinglabs.com/wrong-scheme',
        )
        with self.assertRaises(
            verify_sitemap.SitemapVerificationError,
        ) as raised:
            verify_sitemap.validate_sitemap(
                _xml('https://aishippinglabs.com/', *bad_locations),
                'https://aishippinglabs.com',
            )

        message = str(raised.exception)
        self.assertIn('unexpected origins:', message)
        self.assertIn('entries=6', message)
        self.assertIn('<relative-or-malformed>', message)
        self.assertIn('https://dev.aishippinglabs.com', message)
        self.assertIn('https://example.com', message)

    def test_failure_summary_does_not_log_credentials_or_full_location(self):
        secret = 'sitemap-secret'
        path = '/private/full/path'
        with self.assertRaises(
            verify_sitemap.SitemapVerificationError,
        ) as raised:
            verify_sitemap.validate_sitemap(
                _xml(f'https://user:{secret}@unexpected.example{path}'),
                'https://aishippinglabs.com',
            )

        message = str(raised.exception)
        self.assertIn('https://unexpected.example', message)
        self.assertNotIn(secret, message)
        self.assertNotIn(path, message)

    def test_unavailable_sitemap_fails_closed(self):
        with patch.object(
            verify_sitemap.urllib.request,
            'urlopen',
            side_effect=OSError('connection failed'),
        ):
            with self.assertRaisesRegex(
                verify_sitemap.SitemapVerificationError,
                'sitemap unavailable',
            ):
                verify_sitemap.verify_sitemap_url(
                    'https://aishippinglabs.com/sitemap.xml',
                    'https://aishippinglabs.com',
                )

    def test_cli_failure_prints_only_safe_summary(self):
        error = verify_sitemap.SitemapVerificationError(
            'unexpected origins: https://example.com (1); entries=2',
        )
        stderr = io.StringIO()
        with patch.object(
            verify_sitemap,
            'verify_sitemap_url',
            side_effect=error,
        ):
            with redirect_stderr(stderr):
                exit_code = verify_sitemap.main([
                    'https://aishippinglabs.com/sitemap.xml',
                    'https://aishippinglabs.com',
                ])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stderr.getvalue().strip(),
            '::error ::Sitemap verification failed: '
            'unexpected origins: https://example.com (1); entries=2',
        )
