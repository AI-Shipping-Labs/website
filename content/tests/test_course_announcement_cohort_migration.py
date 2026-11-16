"""Data migration 0092: existing course announcements get a cohort.

Production had three ai-buildcamp announcements, all written for Cohort 4.
The migration maps them explicitly and must never re-send their email.
"""

import datetime
import importlib
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course
from content.models.course_announcement import CourseAnnouncement

_MIGRATION = importlib.import_module(
    'content.migrations.0092_courseannouncement_cohort',
)


def _run():
    _MIGRATION.map_announcements_to_cohorts(django_apps, None)


def _dated_cohort(course, key, start):
    return Cohort.objects.create(
        course=course, name=f'Cohort {key}', external_key=key,
        start_date=start, end_date=start + datetime.timedelta(days=60),
    )


class MapAnnouncementsToCohortsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.buildcamp = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
        )
        cls.cohort3 = _dated_cohort(
            cls.buildcamp, '3', datetime.date(2026, 4, 13),
        )
        cls.cohort4 = _dated_cohort(
            cls.buildcamp, '4', datetime.date(2026, 9, 21),
        )
        cls.other = Course.objects.create(
            title='Other course', slug='other-course-0092', status='published',
        )
        cls.other_cohort = _dated_cohort(
            cls.other, 'spring', datetime.date(2026, 3, 1),
        )

    def _announcement(self, course, published_at, **extra):
        return CourseAnnouncement.objects.create(
            course=course, title='Sent already', body='body',
            status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=published_at,
            email_queued_at=published_at,
            **extra,
        )

    def test_buildcamp_rows_map_to_cohort_4_without_resend(self):
        sent_at = timezone.now() - datetime.timedelta(days=1)
        rows = [self._announcement(self.buildcamp, sent_at) for _ in range(3)]
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue, self.captureOnCommitCallbacks(execute=True) as callbacks:
            _run()
        enqueue.assert_not_called()
        self.assertEqual(callbacks, [])
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.cohort, self.cohort4)
            self.assertEqual(row.email_queued_at, sent_at)
            self.assertEqual(row.status, CourseAnnouncement.STATUS_PUBLISHED)

    def test_other_course_maps_by_publish_date_or_stays_unassigned(self):
        inside = self._announcement(
            self.other,
            timezone.make_aware(datetime.datetime(2026, 3, 15, 12, 0)),
        )
        outside = self._announcement(
            self.other,
            timezone.make_aware(datetime.datetime(2025, 1, 1, 12, 0)),
        )
        _run()
        inside.refresh_from_db()
        outside.refresh_from_db()
        self.assertEqual(inside.cohort, self.other_cohort)
        self.assertIsNone(outside.cohort)

    def test_already_assigned_rows_are_left_alone(self):
        row = self._announcement(
            self.buildcamp, timezone.now(), cohort=self.cohort3,
        )
        _run()
        row.refresh_from_db()
        self.assertEqual(row.cohort, self.cohort3)
