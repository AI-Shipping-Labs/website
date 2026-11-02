"""Tests for the ``backfill_banners`` management command.

Covers the intake ec842352 regression follow-up: the banner pipeline is
fire-and-forget, so events created while the generator was broken keep an
empty ``auto_banner_url`` forever. The command must find exactly those
records and only enqueue renders when asked; the banner pipeline is
mocked at the ``dispatch`` boundary so no Lambda or S3 call is made.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from events.models import Event

DISPATCH = 'integrations.services.banner_generator.dispatch'
RENDER_TASK_PATH = (
    'integrations.services.banner_generator.tasks.render_banner_for_content'
)


class BackfillBannersCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.start = timezone.now() + timedelta(days=3)
        cls.with_banner = Event.objects.create(
            title='Has Banner',
            slug='has-banner',
            description='Covered by a generated banner.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            auto_banner_url='https://cdn.example/banners/event/1-abc.jpg',
            status='upcoming',
            origin='studio',
        )
        cls.missing = Event.objects.create(
            title='Missing Banner',
            slug='missing-banner',
            description='Created while the generator was broken.',
            start_datetime=cls.start + timedelta(days=1),
            end_datetime=cls.start + timedelta(days=1, hours=1),
            status='upcoming',
            origin='studio',
        )

    def test_dry_run_lists_missing_records_and_enqueues_nothing(self):
        out = StringIO()
        with patch(DISPATCH + '.async_task') as task, patch(
            DISPATCH + '.is_enabled', return_value=True,
        ):
            call_command('backfill_banners', stdout=out)
        text = out.getvalue()
        self.assertIn('MISSING event %d Missing Banner' % self.missing.pk, text)
        self.assertIn('DRY RUN: missing=1 enqueued=0 skipped=0', text)
        self.assertNotIn('Has Banner', text)
        task.assert_not_called()

    def test_execute_enqueues_render_only_for_missing_records(self):
        out = StringIO()
        with patch(DISPATCH + '.async_task', return_value='task-1') as task, patch(
            DISPATCH + '.is_enabled', return_value=True,
        ):
            call_command(
                'backfill_banners', '--execute',
                '--content-type', 'event', stdout=out,
            )
        text = out.getvalue()
        self.assertIn(
            'ENQUEUED event %d Missing Banner (task task-1)' % self.missing.pk,
            text,
        )
        self.assertIn('EXECUTE: missing=1 enqueued=1 skipped=0', text)
        self.assertEqual(task.call_count, 1)
        args, kwargs = task.call_args
        self.assertEqual(args[0], RENDER_TASK_PATH)
        self.assertEqual(args[1], 'event')
        self.assertEqual(args[2], self.missing.pk)
        self.assertIn('content sync auto-banner', kwargs['task_name'])

    def test_execute_reports_no_backlog_when_all_records_have_banners(self):
        out = StringIO()
        with patch(DISPATCH + '.async_task') as task, patch(
            DISPATCH + '.is_enabled', return_value=True,
        ):
            call_command(
                'backfill_banners', '--execute',
                '--content-type', 'event', '--id', str(self.with_banner.pk),
                stdout=out,
            )
        self.assertIn('missing=0 enqueued=0 skipped=0', out.getvalue())
        task.assert_not_called()

    def test_execute_refuses_to_enqueue_while_generator_is_disabled(self):
        with patch(DISPATCH + '.is_enabled', return_value=False):
            with self.assertRaises(CommandError):
                call_command(
                    'backfill_banners', '--execute',
                    '--content-type', 'event', '--id', str(self.missing.pk),
                )

    def test_unknown_content_type_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command('backfill_banners', '--content-type', 'nope')
