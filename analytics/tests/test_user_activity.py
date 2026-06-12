"""Tests for the per-user CRM activity timeline (issue #853).

Covers the ``UserActivity`` model, the defensive ``record_activity`` helper,
the instrumentation chokepoints, the backfill command, and the purge task.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from analytics.activity import (
    get_user_activity_retention_days,
    record_activity,
    record_lesson_open,
)
from analytics.models import UserActivity
from analytics.tasks import purge_old_user_activity

User = get_user_model()


class RecordActivityHelperTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='rec@test.com', password='pw',
        )

    def setUp(self):
        # User creation fires the signup signal; clear so each test
        # asserts only on the rows it writes.
        UserActivity.objects.all().delete()

    def test_writes_one_row(self):
        row = record_activity(
            self.user,
            UserActivity.EVENT_PAYMENT,
            label='Payment: Main',
            object_type='tier',
            object_id='main',
        )
        self.assertIsNotNone(row)
        self.assertEqual(
            UserActivity.objects.filter(user=self.user).count(), 1,
        )
        self.assertEqual(row.event_type, UserActivity.EVENT_PAYMENT)
        self.assertEqual(row.label, 'Payment: Main')

    def test_skips_anonymous_user(self):
        from django.contrib.auth.models import AnonymousUser

        result = record_activity(AnonymousUser(), UserActivity.EVENT_SIGNUP)
        self.assertIsNone(result)
        self.assertEqual(UserActivity.objects.count(), 0)

    def test_never_raises_into_caller(self):
        # Force the create to blow up; the helper must swallow it.
        with patch(
            'analytics.activity.UserActivity.objects.create',
            side_effect=ValueError('boom'),
        ):
            result = record_activity(self.user, UserActivity.EVENT_SIGNUP)
        self.assertIsNone(result)

    def test_defaults_occurred_at_to_now(self):
        before = timezone.now()
        row = record_activity(self.user, UserActivity.EVENT_SIGNUP)
        self.assertGreaterEqual(row.occurred_at, before)

    def test_no_pii_fields_on_model(self):
        field_names = {f.name for f in UserActivity._meta.get_fields()}
        for forbidden in ('ip', 'ip_hash', 'user_agent', 'url', 'querystring'):
            self.assertNotIn(forbidden, field_names)


class LessonOpenDedupeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from content.models import Course, Module, Unit

        cls.user = User.objects.create_user(
            email='lesson@test.com', password='pw',
        )
        cls.course = Course.objects.create(
            title='LLM Zoomcamp', slug='llm', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='m1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Intro', slug='intro', sort_order=1,
        )

    def test_records_lesson_open(self):
        row = record_lesson_open(self.user, unit=self.unit)
        self.assertIsNotNone(row)
        self.assertEqual(row.event_type, UserActivity.EVENT_LESSON_OPEN)
        self.assertIn('Intro', row.label)

    def test_dedupes_within_window(self):
        record_lesson_open(self.user, unit=self.unit)
        second = record_lesson_open(self.user, unit=self.unit)
        self.assertIsNone(second)
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                event_type=UserActivity.EVENT_LESSON_OPEN,
            ).count(),
            1,
        )

    def test_records_again_after_window(self):
        first = record_lesson_open(self.user, unit=self.unit)
        # Push the first row outside the dedupe window.
        UserActivity.objects.filter(pk=first.pk).update(
            occurred_at=timezone.now() - timedelta(minutes=45),
        )
        second = record_lesson_open(self.user, unit=self.unit)
        self.assertIsNotNone(second)
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                event_type=UserActivity.EVENT_LESSON_OPEN,
            ).count(),
            2,
        )


class SignupInstrumentationTest(TestCase):
    def test_creating_user_records_signup(self):
        user = User.objects.create_user(email='new@test.com', password='pw')
        signups = UserActivity.objects.filter(
            user=user, event_type=UserActivity.EVENT_SIGNUP,
        )
        self.assertEqual(signups.count(), 1)
        self.assertEqual(signups.first().occurred_at, user.date_joined)


class CourseEnrollInstrumentationTest(TestCase):
    def test_enroll_records_activity(self):
        from content.models import Course
        from content.services.enrollment import ensure_enrollment

        user = User.objects.create_user(email='enr@test.com', password='pw')
        course = Course.objects.create(
            title='Data Eng', slug='de', status='published',
        )
        ensure_enrollment(user, course)
        row = UserActivity.objects.filter(
            user=user, event_type=UserActivity.EVENT_COURSE_ENROLL,
        ).first()
        self.assertIsNotNone(row)
        self.assertIn('Data Eng', row.label)
        self.assertEqual(row.object_id, 'de')
        self.assertTrue(row.target_url)


class PurgeTaskTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='purge@test.com', password='pw',
        )

    def test_purges_only_old_rows(self):
        old = record_activity(self.user, UserActivity.EVENT_SIGNUP)
        UserActivity.objects.filter(pk=old.pk).update(
            occurred_at=timezone.now() - timedelta(days=400),
        )
        recent = record_activity(self.user, UserActivity.EVENT_PAYMENT)

        result = purge_old_user_activity()

        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['cutoff_days'], 365)
        self.assertFalse(UserActivity.objects.filter(pk=old.pk).exists())
        self.assertTrue(UserActivity.objects.filter(pk=recent.pk).exists())

    def test_retention_window_default(self):
        self.assertEqual(get_user_activity_retention_days(), 365)


class BackfillCommandTest(TestCase):
    def test_backfill_is_idempotent_and_seeds_signup(self):
        from django.core.management import call_command

        # Creating a user already writes a forward signup row via the
        # signal. Delete it so the backfill has work to do, then verify the
        # backfill re-derives it and a second run adds nothing.
        user = User.objects.create_user(email='bf@test.com', password='pw')
        UserActivity.objects.all().delete()

        call_command('backfill_user_activity')
        first_count = UserActivity.objects.filter(
            user=user, event_type=UserActivity.EVENT_SIGNUP,
        ).count()
        self.assertEqual(first_count, 1)

        call_command('backfill_user_activity')
        second_count = UserActivity.objects.filter(
            user=user, event_type=UserActivity.EVENT_SIGNUP,
        ).count()
        self.assertEqual(second_count, 1)
