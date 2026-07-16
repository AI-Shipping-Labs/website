"""Regression tests for best-effort admin notification fan-out."""

from datetime import date, timedelta
from unittest.mock import call, patch

from django.contrib import admin
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from content.admin.article import publish_articles
from content.admin.course import publish_courses
from content.admin.download import publish_downloads
from content.models import Article, Course, Download
from events.admin.event import make_upcoming, publish_recordings
from events.models import Event
from notifications.services import NotificationService, notify_safely
from voting.admin.poll import PollAdmin, reopen_polls
from voting.models import Poll


class NotifySafelyTest(SimpleTestCase):
    @patch.object(NotificationService, 'notify')
    def test_success_delegates_exactly_once_without_error_log(self, notify):
        with self.assertNoLogs('notifications.services.safe_notify', level='ERROR'):
            notify_safely('article', 42)

        notify.assert_called_once_with('article', 42)

    @patch.object(
        NotificationService,
        'notify',
        side_effect=RuntimeError('fan-out unavailable'),
    )
    def test_failure_logs_context_and_traceback_without_propagating(self, notify):
        with self.assertLogs(
            'notifications.services.safe_notify',
            level='ERROR',
        ) as captured:
            notify_safely('course', 73)

        notify.assert_called_once_with('course', 73)
        self.assertIn(
            'Notification fan-out failed for course 73',
            captured.output[0],
        )
        self.assertIsNotNone(captured.records[0].exc_info)


class AdminNotificationFanOutTest(TestCase):
    def _failing_fan_out(self):
        return patch.object(
            NotificationService,
            'notify',
            side_effect=RuntimeError('notification outage'),
        )

    def test_articles_continue_per_object_and_keep_publish_fields(self):
        articles = [
            Article.objects.create(
                title=f'Article {index}',
                slug=f'article-{index}',
                date=date(2026, 7, index),
                status='draft',
                published=False,
            )
            for index in (1, 2)
        ]

        with self._failing_fan_out() as notify, self.assertLogs(
            'notifications.services.safe_notify',
            level='ERROR',
        ) as captured:
            publish_articles(
                None,
                None,
                Article.objects.filter(pk__in=[article.pk for article in articles]),
            )

        self.assertCountEqual(
            notify.call_args_list,
            [call('article', article.pk) for article in articles],
        )
        self.assertEqual(len(captured.records), 2)
        for article in articles:
            article.refresh_from_db()
            self.assertTrue(article.published)
            self.assertEqual(article.status, 'published')
            self.assertIsNotNone(article.published_at)

    def test_course_publish_keeps_status_and_kind_on_failure(self):
        course = Course.objects.create(
            title='Course', slug='course', status='draft',
        )

        with self._failing_fan_out() as notify, self.assertLogs(
            'notifications.services.safe_notify', level='ERROR',
        ):
            publish_courses(None, None, Course.objects.filter(pk=course.pk))

        notify.assert_called_once_with('course', course.pk)
        course.refresh_from_db()
        self.assertEqual(course.status, 'published')

    def test_download_publish_keeps_cache_refresh_and_kind_on_failure(self):
        download = Download.objects.create(
            title='Download',
            slug='download',
            file_url='https://example.com/download.pdf',
            published=False,
        )

        with (
            self._failing_fan_out() as notify,
            patch(
                'content.admin.download.refresh_published_downloads_nav_cache',
            ) as refresh_nav,
            self.assertLogs(
                'notifications.services.safe_notify', level='ERROR',
            ),
        ):
            publish_downloads(
                None, None, Download.objects.filter(pk=download.pk),
            )

        notify.assert_called_once_with('download', download.pk)
        refresh_nav.assert_called_once_with()
        download.refresh_from_db()
        self.assertTrue(download.published)

    def test_make_upcoming_notifies_only_events_transitioned_from_draft(self):
        draft = Event.objects.create(
            title='Draft event',
            slug='draft-event',
            status='draft',
            start_datetime=timezone.now() + timedelta(days=1),
        )
        upcoming = Event.objects.create(
            title='Upcoming event',
            slug='upcoming-event',
            status='upcoming',
            start_datetime=timezone.now() + timedelta(days=2),
        )

        with self._failing_fan_out() as notify, self.assertLogs(
            'notifications.services.safe_notify', level='ERROR',
        ):
            make_upcoming(None, None, Event.objects.filter(pk__in=[draft.pk, upcoming.pk]))

        notify.assert_called_once_with('event', draft.pk)
        draft.refresh_from_db()
        upcoming.refresh_from_db()
        self.assertEqual(draft.status, 'upcoming')
        self.assertEqual(upcoming.status, 'upcoming')

    def test_recording_publish_keeps_timestamp_and_kind_on_failure(self):
        recording = Event.objects.create(
            title='Recording',
            slug='recording',
            status='completed',
            published=False,
            start_datetime=timezone.now() - timedelta(days=1),
        )

        with self._failing_fan_out() as notify, self.assertLogs(
            'notifications.services.safe_notify', level='ERROR',
        ):
            publish_recordings(
                None, None, Event.objects.filter(pk=recording.pk),
            )

        notify.assert_called_once_with('recording', recording.pk)
        recording.refresh_from_db()
        self.assertTrue(recording.published)
        self.assertIsNotNone(recording.published_at)

    def test_poll_reopen_keeps_status_and_kind_on_failure(self):
        poll = Poll.objects.create(title='Closed poll', status='closed')

        with self._failing_fan_out() as notify, self.assertLogs(
            'notifications.services.safe_notify', level='ERROR',
        ):
            reopen_polls(None, None, Poll.objects.filter(pk=poll.pk))

        notify.assert_called_once_with('poll', poll.pk)
        poll.refresh_from_db()
        self.assertEqual(poll.status, 'open')

    def test_new_open_poll_notifies_once_but_draft_and_edits_do_not(self):
        poll_admin = PollAdmin(Poll, admin.site)
        open_poll = Poll(title='New open poll', status='open')

        with self._failing_fan_out() as notify, self.assertLogs(
            'notifications.services.safe_notify', level='ERROR',
        ):
            poll_admin.save_model(None, open_poll, None, change=False)

        notify.assert_called_once_with('poll', open_poll.pk)
        self.assertTrue(Poll.objects.filter(pk=open_poll.pk, status='open').exists())

        with patch.object(NotificationService, 'notify') as notify:
            draft_poll = Poll(title='New draft poll', status='closed')
            poll_admin.save_model(None, draft_poll, None, change=False)
            open_poll.title = 'Edited open poll'
            poll_admin.save_model(None, open_poll, None, change=True)

        notify.assert_not_called()
