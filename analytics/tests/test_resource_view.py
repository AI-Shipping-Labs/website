"""Tests for the `resource_view` activity type (issue #773, Phase 2).

Covers the ``record_resource_view`` helper (helper behaviour, dedupe,
forward-only, no double-emit vs lesson_open), and the retention purge.
View-wiring + CRM-render tests live in
``content/tests/test_resource_view_wiring.py`` and
``studio/tests/test_user_activity_section_853.py`` / the timeline tests.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from analytics.activity import record_resource_view
from analytics.models import UserActivity
from analytics.tasks import purge_old_user_activity

User = get_user_model()


class RecordResourceViewHelperTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='rv@test.com', password='pw',
        )

    def setUp(self):
        # User creation fires the signup signal; clear so each test
        # asserts only on the rows it writes.
        UserActivity.objects.all().delete()

    def test_writes_one_row_with_correct_fields(self):
        row = record_resource_view(
            self.user,
            object_type='article',
            object_id='building-llm-agents',
            title='Building LLM agents',
            target_url='/blog/building-llm-agents',
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.event_type, UserActivity.EVENT_RESOURCE_VIEW)
        self.assertEqual(row.object_type, 'article')
        self.assertEqual(row.object_id, 'building-llm-agents')
        self.assertEqual(row.label, 'Viewed article: Building LLM agents')
        self.assertEqual(row.target_url, '/blog/building-llm-agents')
        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            1,
        )

    def test_humanises_curated_link_kind(self):
        row = record_resource_view(
            self.user,
            object_type='curated_link',
            object_id='7',
            title='Awesome MCP servers',
        )
        self.assertEqual(row.label, 'Viewed curated resource: Awesome MCP servers')

    def test_returns_none_for_anonymous(self):
        result = record_resource_view(
            AnonymousUser(),
            object_type='article',
            object_id='x',
            title='X',
        )
        self.assertIsNone(result)
        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            0,
        )

    def test_dedupes_within_window(self):
        record_resource_view(
            self.user, object_type='article', object_id='a', title='A',
        )
        second = record_resource_view(
            self.user, object_type='article', object_id='a', title='A',
        )
        self.assertIsNone(second)
        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            1,
        )

    def test_different_resource_not_deduped(self):
        record_resource_view(
            self.user, object_type='article', object_id='a', title='A',
        )
        other = record_resource_view(
            self.user, object_type='article', object_id='b', title='B',
        )
        self.assertIsNotNone(other)
        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            2,
        )

    def test_records_again_after_window(self):
        first = record_resource_view(
            self.user, object_type='article', object_id='a', title='A',
        )
        # Push the first row outside the 6h dedupe window.
        UserActivity.objects.filter(pk=first.pk).update(
            occurred_at=timezone.now() - timedelta(minutes=361),
        )
        second = record_resource_view(
            self.user, object_type='article', object_id='a', title='A',
        )
        self.assertIsNotNone(second)
        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            2,
        )

    def test_never_raises_into_caller(self):
        from unittest.mock import patch

        with patch(
            'analytics.activity.UserActivity.objects.create',
            side_effect=ValueError('boom'),
        ):
            result = record_resource_view(
                self.user, object_type='article', object_id='a', title='A',
            )
        self.assertIsNone(result)


class ResourceViewRetentionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='rvpurge@test.com', password='pw',
        )

    def setUp(self):
        UserActivity.objects.all().delete()

    def test_purge_removes_old_resource_view_keeps_recent(self):
        old = record_resource_view(
            self.user, object_type='article', object_id='old', title='Old',
        )
        UserActivity.objects.filter(pk=old.pk).update(
            occurred_at=timezone.now() - timedelta(days=400),
        )
        recent = record_resource_view(
            self.user, object_type='article', object_id='new', title='New',
        )

        purge_old_user_activity()

        self.assertFalse(UserActivity.objects.filter(pk=old.pk).exists())
        self.assertTrue(UserActivity.objects.filter(pk=recent.pk).exists())


class ResourceViewBackfillTest(TestCase):
    def test_backfill_creates_no_resource_view_rows(self):
        from django.core.management import call_command

        # Seed data that the backfill DOES derive rows from, to prove the
        # command runs and still emits zero resource_view rows.
        User.objects.create_user(email='bf@test.com', password='pw')
        call_command('backfill_user_activity')

        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            0,
        )
