"""Characterization tests for issue #605 broad-except narrowing.

This file pins the "log and swallow" behavior we explicitly preserved
when narrowing ``except Exception`` clauses in:

- ``integrations/services/github_app.py`` (was ``github_sync/client.py``)
- ``content/sync_parsers/families/classify.py`` (was ``github_sync/orchestration.py``)
- ``content/sync_parsers/families/events.py``
- ``content/sync_parsers/families/courses.py``

Each test raises a specific exception type that the narrowed catch
must still swallow without propagating to the caller. If a future
refactor accidentally re-broadens or re-narrows the catch in a way
that changes which errors get swallowed, these tests fail loudly.
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test import TestCase, override_settings, tag
from django.utils.functional import SimpleLazyObject

from content.sync_parsers.checkout_view import checkout_scope
from content.sync_parsers.families.classify import (
    build_cross_workshop_lookup as _build_cross_workshop_lookup,
)
from content.sync_parsers.families.classify import (
    resolve_workshops_repo_name as _resolve_workshops_repo_name,
)
from content.sync_parsers.families.courses import (
    _resolve_course_description,
)
from integrations.models import ContentSource
from integrations.services.github import sync_content_source
from integrations.services.github_app import (
    _fetch_github_app_private_key_from_secrets_manager,
)


@tag('core')
class SecretsManagerNarrowedCatchTest(TestCase):
    """``_fetch_github_app_private_key_from_secrets_manager`` swallows boto and host-credential errors.

    Before #605 this helper caught ``Exception``. #605 narrowed it to
    ``(BotoCoreError, ClientError)`` (plus ``ImportError`` on the
    ``import boto3`` line). #1899 added ``RuntimeError``: botocore's
    credential refresh machinery raises a bare ``RuntimeError``
    ("Credentials were refreshed, but the refreshed credentials are
    still expired") when the host carries stale SSO or
    ``credential_process`` credentials, and that is an environmental
    failure, not a programmer error -- the sync error path stringifies
    this config value for scrubbing and must never crash on it.
    """

    def test_client_error_returns_empty_string_and_logs(self):
        from botocore.exceptions import ClientError

        # boto3.client(...) is the call we patch; the helper looks it
        # up via ``import boto3`` inside the function.
        fake_boto3 = patch(
            'boto3.client',
            side_effect=ClientError(
                {'Error': {'Code': 'AccessDenied', 'Message': 'no perm'}},
                'GetSecretValue',
            ),
        )
        with fake_boto3, \
             patch(
                 'integrations.services.github_app.logger',
             ) as mock_logger:
            result = _fetch_github_app_private_key_from_secrets_manager(
                'unit-test-secret', 'eu-west-1',
            )
        self.assertEqual(result, '')
        mock_logger.warning.assert_called_once()

    def test_stale_credential_refresh_error_returns_empty_string_and_logs(self):
        # Issue #1899: a fresh agent worktree has no gitignored PEM and the
        # host's AWS session credentials are expired, so the signing-time
        # credential refresh raises bare RuntimeError (not BotoCoreError).
        # The helper must fail soft to '' instead of crashing the caller.
        secret_client = MagicMock()
        refresh_failure = RuntimeError(
            'Credentials were refreshed, but the refreshed credentials are '
            'still expired.'
        )
        secret_client.get_secret_value.side_effect = refresh_failure
        boto3_module = MagicMock()
        boto3_module.client.return_value = secret_client
        with (
            patch.dict(sys.modules, {'boto3': boto3_module}),
            patch('integrations.services.github_app.logger') as mock_logger,
        ):
            result = _fetch_github_app_private_key_from_secrets_manager(
                'unit-test-secret-stale-creds', 'eu-west-1',
            )
        self.assertEqual(result, '')
        self.assertEqual(secret_client.get_secret_value.call_count, 1)
        mock_logger.warning.assert_called_once_with(
            'Failed to fetch secret %s: %s',
            'unit-test-secret-stale-creds', refresh_failure,
        )


@tag('core')
class WorkshopsRepoNameDatabaseErrorNarrowedCatchTest(TestCase):
    """``_resolve_workshops_repo_name`` swallows DatabaseError only.

    Before #605 the DB lookup was wrapped in ``except Exception``. It is
    now ``except DatabaseError``, which matches the documented
    "DB briefly unreachable" case while letting programmer errors
    (AttributeError, etc.) surface.
    """

    def test_operational_error_falls_through_to_default(self):
        with patch(
            'content.sync_parsers.families.classify.'
            'PackageContentSource.objects.filter',
            side_effect=OperationalError('database is locked'),
        ):
            result = _resolve_workshops_repo_name(source=None)
        # Fallback default is the production workshops repo string.
        self.assertEqual(result, 'AI-Shipping-Labs/workshops')


@tag('core')
class CrossWorkshopLookupParseFailureNarrowedCatchTest(TestCase):
    """``_build_cross_workshop_lookup`` swallows ValueError / OSError only.

    The lookup walks every workshop folder and parses ``workshop.yaml``
    plus each page's frontmatter. Before #605 a bare ``except
    Exception`` skipped unparseable files. It is now narrowed to
    ``(ValueError, OSError)`` — the realistic surface of
    ``_parse_yaml_file`` / ``_parse_markdown_file``. The test forces
    each kind and confirms the lookup keeps building rather than
    aborting.
    """

    def test_value_error_on_workshop_yaml_is_swallowed(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            workshop_dir = os.path.join(repo_dir, '2026-05-15-bad')
            os.makedirs(workshop_dir)
            yaml_path = os.path.join(workshop_dir, 'workshop.yaml')
            # Top-level YAML list -> ``_parse_yaml_file`` raises ValueError.
            with open(yaml_path, 'w', encoding='utf-8') as f:
                f.write('- not\n- a\n- mapping\n')

            errors = []
            with checkout_scope(repo_dir):
                lookup = _build_cross_workshop_lookup(
                    [workshop_dir], repo_dir, errors=errors,
                )
        # Bad workshop.yaml -> entry skipped silently; no crash.
        self.assertEqual(lookup, {})

    def test_os_error_on_workshop_yaml_is_swallowed(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            workshop_dir = os.path.join(repo_dir, '2026-05-15-missing')
            os.makedirs(workshop_dir)
            # No workshop.yaml at all -> ``open`` raises FileNotFoundError
            # (a subclass of OSError) inside ``_parse_yaml_file``.
            errors = []
            with checkout_scope(repo_dir):
                lookup = _build_cross_workshop_lookup(
                    [workshop_dir], repo_dir, errors=errors,
                )
        self.assertEqual(lookup, {})


@tag('core')
class CourseReadmeNarrowedCatchTest(TestCase):
    """``_resolve_course_description`` swallows ValueError / OSError only.

    Before #605 the helper caught ``Exception`` around
    ``_parse_markdown_file``. It is now narrowed to ``(ValueError,
    OSError)``. A bad frontmatter file must still yield ``''`` plus a
    logged warning — anything else is a real bug and must propagate.
    """

    def test_bad_yaml_frontmatter_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as course_dir:
            readme_path = os.path.join(course_dir, 'README.md')
            # Broken YAML inside the frontmatter delimiters -> the
            # ``frontmatter.load`` call raises yaml.YAMLError which
            # ``_parse_markdown_file`` re-wraps as ValueError.
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write('---\n: : not yaml\n---\n# Hello\n')

            with patch(
                'content.sync_parsers.families.courses.logger',
            ) as mock_logger:
                with checkout_scope(course_dir):
                    result = _resolve_course_description(
                        {},  # course_data with no 'description' key
                        course_dir,
                        [],  # course_ignore_patterns
                    )

        self.assertEqual(result, '')
        mock_logger.warning.assert_called_once()


@tag('core')
class SyncFunnelPrivateKeyResolverErrorTest(TestCase):
    """Funnel regression for #1901: a raising private-key resolver never
    crashes ``sync_content_source``.

    The package error-normalization path
    (``community_base.content_sync.orchestration._safe_error``) stringifies
    the redaction canary ``str(conf.get('CONTENT_SYNC_GITHUB_PRIVATE_KEY'))``.
    On this site that value is a ``SimpleLazyObject`` (``website/settings.py``)
    resolving through
    ``integrations.services.github_app._resolve_github_app_private_key`` ->
    ``_fetch_github_app_private_key_from_secrets_manager``. Pre-#1899 the
    fetch re-raised bare ``RuntimeError`` (stale host SSO credentials), the
    package per-parser ``except`` handler raised while formatting the canary,
    the outer handler re-forced the (uncached) lazy canary and raised again,
    and the whole sync errored before its caller saw a ``SyncLog``.

    Each test installs a FRESH ``SimpleLazyObject`` under
    ``COMMUNITY_BASE['CONTENT_SYNC_GITHUB_PRIVATE_KEY']`` so a canary already
    forced (and cached as ``''``) by an earlier test in the same worker
    process cannot bypass the raiser, then drives the real
    ``sync_content_source`` -> package engine funnel over a one-file repo
    whose article is missing ``content_id`` (fixture mirrors
    ``comments/tests/test_content_id.py``). ``conf.get`` reads
    ``settings.COMMUNITY_BASE`` live, so the fresh lazy object is exactly
    what ``_safe_error`` forces on every canary stringify.

    Design note: the package re-forces the canary once per failing handler
    (inner + outer). A resolver that raises on EVERY force therefore escapes
    the sync entirely -- that escape is the documented package-side gap,
    hardening of which is out of scope for #1901 (package issue). The
    site-side contract pinned here: a resolver failure on the first force is
    bounded by the outer handler into a recorded sync error once the next
    resolution succeeds, and the rich per-file ``missing content_id`` error
    collected before the parser family raised still reaches
    ``SyncLog.errors``.
    """

    def _write_article_without_content_id(self, repo_dir):
        """One article file with a ``date`` but no ``content_id`` (#310)."""
        import datetime as dt

        import frontmatter as fm

        post = fm.Post('Body', title='No ID', slug='no-id', date=dt.date(2026, 1, 1))
        with open(os.path.join(repo_dir, 'no-id.md'), 'wb') as f:
            fm.dump(post, f)

    def _private_key_override(self, resolver_callable):
        """Swap the lazy canary for a fresh one wrapping ``resolver_callable``."""
        return override_settings(COMMUNITY_BASE={
            **settings.COMMUNITY_BASE,
            'CONTENT_SYNC_GITHUB_PRIVATE_KEY': SimpleLazyObject(resolver_callable),
        })

    @staticmethod
    def _raise_once_then_recover(exc):
        """Resolver that fails once (first force) then returns ``''``."""
        calls = []

        def _resolver():
            calls.append(1)
            if len(calls) == 1:
                raise exc
            return ''

        return _resolver, calls

    @staticmethod
    def _error_texts(log):
        return [
            entry.get('error', '') for entry in (log.errors or [])
            if isinstance(entry, dict)
        ]

    def _assert_missing_content_id_recorded(self, log):
        self.assertTrue(
            any('missing content_id' in text for text in self._error_texts(log)),
            f"Expected a 'missing content_id' error, got: {log.errors}",
        )

    def test_runtime_error_from_resolver_is_bounded_and_preserves_content_id_error(self):
        raiser, calls = self._raise_once_then_recover(
            RuntimeError('unit-test resolver failure'),
        )
        source = ContentSource.objects.create(repo_name='test/funnel-runtime-error')

        with self._private_key_override(raiser), \
                tempfile.TemporaryDirectory() as repo_dir:
            self._write_article_without_content_id(repo_dir)
            log = sync_content_source(source, repo_dir=repo_dir)

        # Interception proof: the funnel forced the canary exactly twice --
        # the first force raised inside the package per-parser handler, the
        # second recovered inside the outer handler.
        self.assertEqual(len(calls), 2)
        # No exception escaped: the sync completed and recorded the failure.
        self.assertEqual(log.status, 'failed')
        self._assert_missing_content_id_recorded(log)
        self.assertTrue(
            any('unit-test resolver failure' in text for text in self._error_texts(log)),
            f'Expected the bounded resolver failure, got: {log.errors}',
        )

    def test_improperly_configured_from_resolver_is_bounded_and_preserves_content_id_error(self):
        raiser, calls = self._raise_once_then_recover(
            ImproperlyConfigured('unit-test resolver misconfiguration'),
        )
        source = ContentSource.objects.create(
            repo_name='test/funnel-improperly-configured',
        )

        with self._private_key_override(raiser), \
                tempfile.TemporaryDirectory() as repo_dir:
            self._write_article_without_content_id(repo_dir)
            log = sync_content_source(source, repo_dir=repo_dir)

        self.assertEqual(len(calls), 2)
        self.assertEqual(log.status, 'failed')
        self._assert_missing_content_id_recorded(log)
        self.assertTrue(
            any(
                'unit-test resolver misconfiguration' in text
                for text in self._error_texts(log)
            ),
            f'Expected the bounded resolver failure, got: {log.errors}',
        )

    def test_stale_credential_runtime_error_from_secrets_manager_keeps_sync_partial(self):
        """#1899 revert-detector at the funnel.

        Drives the REAL resolver and fetch with a fake boto3 whose
        ``get_secret_value`` raises the bare stale-credential
        ``RuntimeError``. Post-#1899 the fetch swallows it, returns ``''``,
        and the sync completes ``partial`` with the ``missing content_id``
        error intact. If the fetch's catch were re-narrowed to
        ``(BotoCoreError, ClientError)``, the ``RuntimeError`` would escape
        the fetch, the per-parser handler's canary stringify would raise,
        and the outer handler would re-force the uncached canary and raise
        out of ``sync_content_source`` -- this test would error.
        """
        import integrations.services.github_app as github_app

        secret_client = MagicMock()
        refresh_failure = RuntimeError(
            'Credentials were refreshed, but the refreshed credentials are '
            'still expired.'
        )
        secret_client.get_secret_value.side_effect = refresh_failure
        boto3_module = MagicMock()
        boto3_module.client.return_value = secret_client

        def fake_get_config(key, default='', **kwargs):
            if key == 'GITHUB_APP_PRIVATE_KEY':
                return ''
            if key == 'GITHUB_APP_PRIVATE_KEY_SECRET_ID':
                return 'unit-test-secret-funnel'
            if key == 'GITHUB_APP_PRIVATE_KEY_SECRET_REGION':
                return 'eu-west-1'
            return default

        def real_resolver():
            # Attribute resolved at call time, so the funnel runs the real
            # resolver + fetch (not a captured pre-patch reference).
            return github_app._resolve_github_app_private_key()

        source = ContentSource.objects.create(repo_name='test/funnel-secrets-manager')

        with self._private_key_override(real_resolver), \
                patch.dict(sys.modules, {'boto3': boto3_module}), \
                patch(
                    'integrations.services.github_app.get_config',
                    side_effect=fake_get_config,
                ), \
                patch('integrations.services.github_app.logger') as mock_logger, \
                tempfile.TemporaryDirectory() as repo_dir:
            self._write_article_without_content_id(repo_dir)
            log = sync_content_source(source, repo_dir=repo_dir)

        # Interception proof: the funnel reached Secrets Manager through the
        # real resolver + fetch, and the #1899 catch swallowed the failure.
        self.assertEqual(secret_client.get_secret_value.call_count, 1)
        self.assertTrue(mock_logger.warning.called)
        self.assertEqual(log.status, 'partial')
        self._assert_missing_content_id_recorded(log)
        # The boto-level failure itself never surfaced anywhere in the log.
        self.assertFalse(
            any('refreshed credentials' in text for text in self._error_texts(log)),
            f'RuntimeError leaked into SyncLog.errors: {log.errors}',
        )
