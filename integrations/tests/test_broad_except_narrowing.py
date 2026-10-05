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

from django.db import OperationalError
from django.test import TestCase, tag

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
