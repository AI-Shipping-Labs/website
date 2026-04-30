import uuid
from datetime import date
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from content.models import Article, Course
from content.services.qa_fixture_cleanup import find_cleanup_candidates
from website.test_database_guard import (
    UnsafeTestDatabaseError,
    assert_playwright_database_is_safe,
    is_database_test_scoped,
)


class PlaywrightDatabaseGuardTests(SimpleTestCase):
    def test_blocks_repository_db_sqlite3(self):
        base_dir = Path('/repo')
        database_settings = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(base_dir / 'db.sqlite3'),
        }

        with self.assertRaisesMessage(
            UnsafeTestDatabaseError,
            'Run `uv run pytest playwright_tests/...`',
        ):
            assert_playwright_database_is_safe(database_settings, base_dir=base_dir)

    def test_allows_sqlite_test_database(self):
        database_settings = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': '/tmp/test_ai_shipping_labs.sqlite3',
        }

        self.assertTrue(is_database_test_scoped(database_settings, base_dir=Path('/repo')))

    def test_blocks_non_test_named_database(self):
        database_settings = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'aishippinglabs',
        }

        with self.assertRaises(UnsafeTestDatabaseError):
            assert_playwright_database_is_safe(database_settings, base_dir=Path('/repo'))


class CleanupQaFixturesCommandTests(TestCase):
    def test_dry_run_lists_candidates_without_deleting(self):
        Course.objects.create(
            title='QA 392 Gated Course',
            slug='qa-392-gated-course',
            status='published',
        )

        out = StringIO()
        call_command('cleanup_qa_fixtures', stdout=out)

        output = out.getvalue()
        self.assertIn('Dry run: would delete 1 likely unsynced QA/test fixture row', output)
        self.assertIn('QA 392 Gated Course', output)
        self.assertEqual(Course.objects.filter(slug='qa-392-gated-course').count(), 1)

    def test_apply_deletes_candidates(self):
        Course.objects.create(
            title='QA 392 Gated Course',
            slug='qa-392-gated-course',
            status='published',
        )

        out = StringIO()
        call_command('cleanup_qa_fixtures', '--apply', stdout=out)

        self.assertIn('Deleted 1 row', out.getvalue())
        self.assertFalse(Course.objects.filter(slug='qa-392-gated-course').exists())

    def test_synced_content_is_protected(self):
        Course.objects.create(
            title='QA Synced Course',
            slug='qa-synced-course',
            source_repo='AI-Shipping-Labs/content',
            status='published',
        )
        Course.objects.create(
            title='QA Content ID Course',
            slug='qa-content-id-course',
            content_id=uuid.uuid4(),
            status='published',
        )
        Article.objects.create(
            title='Architecture Walk-through',
            slug='architecture-walk-through',
            date=date(2026, 1, 1),
            source_repo='AI-Shipping-Labs/content',
            content_id=uuid.uuid4(),
        )

        candidates = find_cleanup_candidates()

        self.assertEqual(candidates, [])

        out = StringIO()
        call_command('cleanup_qa_fixtures', '--apply', stdout=out)

        self.assertEqual(Course.objects.count(), 2)
        self.assertEqual(Article.objects.count(), 1)
        self.assertIn('No likely unsynced QA/test fixture rows found.', out.getvalue())
