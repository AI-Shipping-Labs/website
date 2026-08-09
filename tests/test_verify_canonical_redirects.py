"""Tests for the deployed canonical-host redirect verifier (#1382)."""

import importlib.util
import io
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, tag


def _load_verifier_module():
    module_path = (
        Path(__file__).resolve().parent.parent
        / 'deploy'
        / 'verify_canonical_redirects.py'
    )
    spec = importlib.util.spec_from_file_location(
        'deploy_verify_canonical_redirects',
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier_module()
ORIGIN = 'https://aishippinglabs.com'


def _successful_request_once(calls):
    def request_once(url, timeout):
        calls.append((url, timeout))
        parsed = verifier.urlsplit(url)
        if parsed.scheme == 'https' and parsed.hostname == 'aishippinglabs.com':
            return 200, None
        return 301, f'{ORIGIN}{parsed.path}' + (
            f'?{parsed.query}' if parsed.query else ''
        )

    return request_once


@tag('core')
class CanonicalRedirectVerifierTest(SimpleTestCase):
    def test_accepts_exact_root_and_path_query_one_hop_matrix(self):
        calls = []

        observations = verifier.verify_canonical_redirects(
            f'{ORIGIN}/',
            timeout=7,
            request_once=_successful_request_once(calls),
        )

        self.assertEqual(observations, 8)
        self.assertEqual(len(calls), 8)
        self.assertIn(
            ('http://www.aishippinglabs.com/', 7),
            calls,
        )
        self.assertIn(
            (
                'https://www.aishippinglabs.com/blog'
                '?utm_source=canonical-redirect-verifier',
                7,
            ),
            calls,
        )

    def test_rejects_wrong_status(self):
        with self.assertRaisesRegex(
            verifier.CanonicalRedirectVerificationError,
            'did not return HTTP 301',
        ):
            verifier.verify_canonical_redirects(
                ORIGIN,
                request_once=lambda _url, _timeout: (302, ORIGIN),
            )

    def test_rejects_port_alias_intermediate_or_lost_query_target(self):
        mutations = (
            lambda expected: expected.replace(
                'aishippinglabs.com',
                'aishippinglabs.com:443',
            ),
            lambda expected: expected.replace(
                'aishippinglabs.com',
                'www.aishippinglabs.com',
            ),
            lambda expected: expected.split('?', 1)[0],
        )
        for mutate_location in mutations:
            with self.subTest(mutation=mutate_location):
                def request_once(url, _timeout):
                    parsed = verifier.urlsplit(url)
                    if (
                        parsed.scheme == 'https'
                        and parsed.hostname == 'aishippinglabs.com'
                    ):
                        return 200, None
                    expected = f'{ORIGIN}{parsed.path}' + (
                        f'?{parsed.query}' if parsed.query else ''
                    )
                    # Let the root matrix pass so every mutation is also
                    # exercised against the query-bearing path.
                    if not parsed.query:
                        return 301, expected
                    return 301, mutate_location(expected)

                with self.assertRaisesRegex(
                    verifier.CanonicalRedirectVerificationError,
                    'not the clean canonical URL',
                ):
                    verifier.verify_canonical_redirects(
                        ORIGIN,
                        request_once=request_once,
                    )

    def test_rejects_a_second_redirect_from_canonical_target(self):
        def request_once(url, _timeout):
            parsed = verifier.urlsplit(url)
            if parsed.scheme == 'https' and parsed.hostname == 'aishippinglabs.com':
                return 301, f'{ORIGIN}/other'
            return 301, f'{ORIGIN}{parsed.path}' + (
                f'?{parsed.query}' if parsed.query else ''
            )

        with self.assertRaisesRegex(
            verifier.CanonicalRedirectVerificationError,
            'more than one redirect hop',
        ):
            verifier.verify_canonical_redirects(
                ORIGIN,
                request_once=request_once,
            )

    def test_rejects_non_clean_canonical_origins(self):
        values = (
            'http://aishippinglabs.com',
            'https://aishippinglabs.com:443',
            'https://aishippinglabs.com/path',
            'https://user:secret@aishippinglabs.com',
        )
        for value in values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'clean HTTPS origin'):
                    verifier.verify_canonical_redirects(value)

    def test_cli_failure_is_concise(self):
        stderr = io.StringIO()
        with patch.object(
            verifier,
            'verify_canonical_redirects',
            side_effect=verifier.CanonicalRedirectVerificationError(
                'redirect endpoint unavailable',
            ),
        ):
            with redirect_stderr(stderr):
                exit_code = verifier.main([ORIGIN])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stderr.getvalue().strip(),
            '::error ::Canonical redirect verification failed: '
            'redirect endpoint unavailable',
        )
