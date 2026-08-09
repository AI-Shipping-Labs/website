"""Tests for production post-deploy robots verification (issue #1380)."""

import importlib.util
import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, tag


def _load_verify_robots_module():
    module_path = Path(__file__).resolve().parent.parent / 'deploy' / 'verify_robots.py'
    spec = importlib.util.spec_from_file_location('deploy_verify_robots', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_robots = _load_verify_robots_module()
EXPECTED_SITEMAP = 'https://aishippinglabs.com/sitemap.xml'
VALID_BODY = (
    b'User-agent: *\n'
    b'Allow: /\n'
    b'\n'
    b'Sitemap: https://aishippinglabs.com/sitemap.xml\n'
)


@tag('core')
class RobotsValidationTest(SimpleTestCase):
    def test_accepts_exact_plain_text_production_contract(self):
        result = verify_robots.validate_robots(
            VALID_BODY,
            'text/plain; charset=utf-8',
            EXPECTED_SITEMAP,
        )

        self.assertEqual(result['user-agent'], 1)
        self.assertEqual(result['allow'], 1)
        self.assertEqual(result['sitemap'], 1)

    def test_rejects_non_plain_text_and_invalid_utf8(self):
        cases = (
            (VALID_BODY, 'text/html', 'not plain text'),
            (b'\xff', 'text/plain', 'not valid UTF-8'),
        )
        for body, content_type, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    verify_robots.RobotsVerificationError,
                    message,
                ):
                    verify_robots.validate_robots(
                        body,
                        content_type,
                        EXPECTED_SITEMAP,
                    )

    def test_rejects_disallow_and_missing_or_duplicate_allow_all_group(self):
        cases = (
            (
                VALID_BODY + b'Disallow: /private\n',
                'Disallow directive present',
            ),
            (
                VALID_BODY.replace(b'User-agent: *\n', b''),
                'exactly one User-agent',
            ),
            (
                VALID_BODY.replace(
                    b'User-agent: *\n',
                    b'User-agent: *\nUser-agent: Googlebot\n',
                ),
                'exactly one User-agent',
            ),
            (
                VALID_BODY.replace(b'Allow: /\n', b''),
                'exactly one Allow',
            ),
            (
                VALID_BODY.replace(b'Allow: /\n', b'Allow: /\nAllow: /\n'),
                'exactly one Allow',
            ),
        )
        for body, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    verify_robots.RobotsVerificationError,
                    message,
                ):
                    verify_robots.validate_robots(
                        body,
                        'text/plain',
                        EXPECTED_SITEMAP,
                    )

    def test_rejects_missing_duplicate_or_unexpected_sitemap(self):
        wrong_targets = (
            'https://dev.aishippinglabs.com/sitemap.xml',
            'https://www.aishippinglabs.com/sitemap.xml',
            'https://example.com/sitemap.xml',
            '/sitemap.xml',
        )
        cases = [
            (
                VALID_BODY.replace(
                    b'Sitemap: https://aishippinglabs.com/sitemap.xml\n',
                    b'',
                ),
                'exactly one Sitemap',
            ),
            (
                VALID_BODY + b'Sitemap: https://aishippinglabs.com/sitemap.xml\n',
                'exactly one Sitemap',
            ),
        ]
        cases.extend(
            (
                VALID_BODY.replace(
                    EXPECTED_SITEMAP.encode(),
                    target.encode(),
                ),
                'unexpected Sitemap target',
            )
            for target in wrong_targets
        )

        for body, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    verify_robots.RobotsVerificationError,
                    message,
                ):
                    verify_robots.validate_robots(
                        body,
                        'text/plain',
                        EXPECTED_SITEMAP,
                    )

    def test_rejects_unavailable_and_non_200_response(self):
        class Non200Response:
            status = 404
            headers = {'Content-Type': 'text/plain'}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'not found'

        with patch.object(
            verify_robots.urllib.request,
            'urlopen',
            return_value=Non200Response(),
        ):
            with self.assertRaisesRegex(
                verify_robots.RobotsVerificationError,
                'not 200',
            ):
                verify_robots.verify_robots_url(
                    'https://aishippinglabs.com/robots.txt',
                    EXPECTED_SITEMAP,
                )

        with patch.object(
            verify_robots.urllib.request,
            'urlopen',
            side_effect=OSError('connection failed with private detail'),
        ):
            with self.assertRaisesRegex(
                verify_robots.RobotsVerificationError,
                'unavailable',
            ):
                verify_robots.verify_robots_url(
                    'https://aishippinglabs.com/robots.txt',
                    EXPECTED_SITEMAP,
                )

    def test_cli_failure_prints_only_safe_summary(self):
        secret = 'do-not-print-this-body-value'
        error = verify_robots.RobotsVerificationError(
            'unexpected Sitemap target',
        )
        stderr = io.StringIO()
        with patch.object(
            verify_robots,
            'verify_robots_url',
            side_effect=error,
        ):
            with redirect_stderr(stderr):
                exit_code = verify_robots.main([
                    'https://aishippinglabs.com/robots.txt',
                    f'https://user:{secret}@aishippinglabs.com/sitemap.xml',
                ])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stderr.getvalue().strip(),
            '::error ::Robots verification failed: unexpected Sitemap target',
        )
        self.assertNotIn(secret, stderr.getvalue())

    def test_prod_workflow_runs_robots_after_ping_and_sitemap_before_success(self):
        workflow_path = (
            Path(__file__).resolve().parent.parent
            / '.github' / 'workflows' / 'deploy-prod.yml'
        )
        workflow = workflow_path.read_text()
        ping_index = workflow.index('Prod verified — /ping returned')
        sitemap_index = workflow.index('deploy/verify_sitemap.py', ping_index)
        robots_index = workflow.index('deploy/verify_robots.py', sitemap_index)
        success_index = workflow.index('exit 0', robots_index)

        self.assertLess(ping_index, sitemap_index)
        self.assertLess(sitemap_index, robots_index)
        self.assertLess(robots_index, success_index)


class RobotsValidationUnexpectedDirectiveTest(SimpleTestCase):
    def test_extra_or_malformed_directive_fails_without_echoing_body(self):
        secret = 'private-body-detail'
        cases = (
            VALID_BODY + f'Crawl-delay: {secret}\n'.encode(),
            VALID_BODY + f'{secret}\n'.encode(),
        )
        for body in cases:
            with self.subTest(body_kind='extra-or-malformed'):
                with self.assertRaises(
                    verify_robots.RobotsVerificationError,
                ) as raised:
                    verify_robots.validate_robots(
                        body,
                        'text/plain',
                        EXPECTED_SITEMAP,
                    )
                self.assertNotIn(secret, str(raised.exception))
