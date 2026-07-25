"""Instructor email sync + auto-link on sync (issue #1345).

The instructor sync dispatcher parses an optional ``email:`` key, folds it
into change detection, and best-effort auto-links a NULL ``user`` FK to the
verified platform account of the same email without ever failing the sync or
overwriting an operator override.
"""

import os
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Instructor
from integrations.models import ContentSource
from integrations.services.github import sync_content_source

User = get_user_model()


class _SyncFixture(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='instructor-email-sync-')
        self.instructors_dir = os.path.join(self.temp_dir, 'instructors')
        os.makedirs(self.instructors_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.instructors_dir, name), 'w') as f:
            f.write(text)

    def _source(self):
        return ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content', is_private=False,
        )


class InstructorEmailParseTest(_SyncFixture):
    def test_email_parsed_into_field(self):
        self._write('ada.yaml', 'id: ada\nname: Ada\nemail: ada@test.com\n')
        sync_content_source(self._source(), repo_dir=self.temp_dir)
        self.assertEqual(
            Instructor.objects.get(instructor_id='ada').email, 'ada@test.com',
        )

    def test_missing_email_leaves_empty(self):
        self._write('eve.yaml', 'id: eve\nname: Eve\n')
        sync_content_source(self._source(), repo_dir=self.temp_dir)
        self.assertEqual(Instructor.objects.get(instructor_id='eve').email, '')

    def test_email_participates_in_change_detection(self):
        source = self._source()
        self._write('ada.yaml', 'id: ada\nname: Ada\nemail: ada@test.com\n')
        sync_content_source(source, repo_dir=self.temp_dir)

        # Unchanged re-sync stays unchanged.
        log_unchanged = sync_content_source(source, repo_dir=self.temp_dir)
        self.assertEqual(log_unchanged.items_unchanged, 1)
        self.assertEqual(log_unchanged.items_updated, 0)

        # Changing the email is detected as an update.
        self._write('ada.yaml', 'id: ada\nname: Ada\nemail: new@test.com\n')
        log_updated = sync_content_source(source, repo_dir=self.temp_dir)
        self.assertEqual(log_updated.items_updated, 1)
        self.assertEqual(
            Instructor.objects.get(instructor_id='ada').email, 'new@test.com',
        )


class InstructorAutoLinkOnSyncTest(_SyncFixture):
    def test_sync_auto_links_unlinked_instructor(self):
        user = User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        self._write('ada.yaml', 'id: ada\nname: Ada\nemail: ada@test.com\n')
        sync_content_source(self._source(), repo_dir=self.temp_dir)
        self.assertEqual(
            Instructor.objects.get(instructor_id='ada').user, user,
        )

    def test_sync_does_not_overwrite_operator_override(self):
        work = User.objects.create_user(
            email='ben-work@test.com', password='pw', email_verified=True,
        )
        User.objects.create_user(
            email='ben-personal@test.com', password='pw', email_verified=True,
        )
        source = self._source()
        self._write(
            'ben.yaml', 'id: ben\nname: Ben\nemail: ben-personal@test.com\n',
        )
        sync_content_source(source, repo_dir=self.temp_dir)

        ben = Instructor.objects.get(instructor_id='ben')
        ben.user = work
        ben.save(update_fields=['user'])

        # Re-sync must not steal the override even though a verified match
        # for the yaml email exists.
        sync_content_source(source, repo_dir=self.temp_dir)
        ben.refresh_from_db()
        self.assertEqual(ben.user, work)

    def test_sync_leaves_unverified_match_unlinked(self):
        User.objects.create_user(
            email='cleo@test.com', password='pw', email_verified=False,
        )
        self._write(
            'cleo.yaml', 'id: cleo\nname: Cleo\nemail: cleo@test.com\n',
        )
        sync_content_source(self._source(), repo_dir=self.temp_dir)
        self.assertIsNone(
            Instructor.objects.get(instructor_id='cleo').user_id,
        )

    def test_empty_email_never_auto_linked(self):
        self._write('eve.yaml', 'id: eve\nname: Eve\n')
        sync_content_source(self._source(), repo_dir=self.temp_dir)
        self.assertIsNone(
            Instructor.objects.get(instructor_id='eve').user_id,
        )

    def test_auto_link_error_does_not_break_sync(self):
        """A raised error inside auto-link is swallowed (best-effort): the
        instructor is still upserted and the sync completes successfully.
        """
        User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        self._write('ada.yaml', 'id: ada\nname: Ada\nemail: ada@test.com\n')
        with patch(
            'content.services.instructor_linking.auto_link_instructor',
            side_effect=RuntimeError('boom'),
        ) as mocked:
            log = sync_content_source(self._source(), repo_dir=self.temp_dir)

        self.assertTrue(mocked.called)
        # Sync did not error out on the linking failure.
        self.assertEqual(log.status, 'success')
        # The instructor row was still created; only the (best-effort) link
        # was skipped.
        ada = Instructor.objects.get(instructor_id='ada')
        self.assertEqual(ada.email, 'ada@test.com')
        self.assertIsNone(ada.user_id)
