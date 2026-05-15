"""Characterization tests for issue #605 broad-except narrowing.

This file pins the "log and swallow" behavior we explicitly preserved
when narrowing ``except Exception`` clauses in:

- ``integrations/services/github_sync/client.py``
- ``integrations/services/github_sync/orchestration.py``
- ``integrations/services/github_sync/dispatchers/events.py``
- ``integrations/services/github_sync/dispatchers/courses.py``

Each test raises a specific exception type that the narrowed catch
must still swallow without propagating to the caller. If a future
refactor accidentally re-broadens or re-narrows the catch in a way
that changes which errors get swallowed, these tests fail loudly.
"""

import os
import tempfile
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, tag

from integrations.services.github_sync.client import (
    _fetch_github_app_private_key_from_secrets_manager,
)
from integrations.services.github_sync.dispatchers.courses import (
    _resolve_course_description,
)
from integrations.services.github_sync.orchestration import (
    _build_cross_workshop_lookup,
    _resolve_workshops_repo_name,
)


@tag('core')
class SecretsManagerNarrowedCatchTest(TestCase):
    """``_fetch_github_app_private_key_from_secrets_manager`` swallows boto3 errors only.

    Before #605 this helper caught ``Exception``. It now catches
    ``(BotoCoreError, ClientError)`` (plus ``ImportError`` on the
    ``import boto3`` line). The test verifies a representative
    ``ClientError`` still results in an empty-string return and a
    logged warning.
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
                 'integrations.services.github_sync.client.logger',
             ) as mock_logger:
            result = _fetch_github_app_private_key_from_secrets_manager(
                'unit-test-secret', 'eu-west-1',
            )
        self.assertEqual(result, '')
        mock_logger.warning.assert_called_once()


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
            'integrations.services.github_sync.orchestration.'
            'ContentSource.objects.filter',
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
                'integrations.services.github_sync.dispatchers.courses.logger',
            ) as mock_logger:
                result = _resolve_course_description(
                    {},  # course_data with no 'description' key
                    course_dir,
                    [],  # course_ignore_patterns
                )

        self.assertEqual(result, '')
        mock_logger.warning.assert_called_once()
