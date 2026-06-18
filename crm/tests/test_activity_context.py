"""Tests for shared CRM activity context serialization (issue #1054)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from analytics.models import UserActivity
from crm.services.activity_context import (
    ACTIVITY_CATEGORY_LEARNING,
    build_activity_context,
    serialize_activity_for_api,
)

User = get_user_model()


class ActivityContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='activity-context@test.com', password='pw',
        )
        from content.models import Course, Module, Unit
        from events.models import Event

        cls.course = Course.objects.create(
            title='Context Course',
            slug='context-course',
            status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title='Module A',
            slug='module-a',
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Lesson A',
            slug='lesson-a',
            sort_order=1,
        )
        cls.event = Event.objects.create(
            title='Context Event',
            slug='context-event',
            start_datetime=timezone.now() + timezone.timedelta(days=3),
            status='upcoming',
        )
        UserActivity.objects.all().delete()

    def setUp(self):
        UserActivity.objects.all().delete()

    def _add(
        self,
        event_type,
        label,
        *,
        minutes_ago=0,
        target_url='',
        object_type='',
        object_id='',
    ):
        return UserActivity.objects.create(
            user=self.member,
            event_type=event_type,
            label=label,
            target_url=target_url,
            object_type=object_type,
            object_id=object_id,
            occurred_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
        )

    def test_filters_by_category_and_returns_counts(self):
        self._add(UserActivity.EVENT_SIGNUP, 'Signed up', minutes_ago=5)
        self._add(
            UserActivity.EVENT_COURSE_ENROLL,
            'Enrolled in Context Course',
            object_type='course',
            object_id=self.course.slug,
            minutes_ago=4,
        )
        self._add(
            UserActivity.EVENT_EVENT_REGISTER,
            'Registered for Context Event',
            object_type='event',
            object_id=self.event.slug,
            minutes_ago=3,
        )
        self._add(
            UserActivity.EVENT_RESOURCE_VIEW,
            'Viewed article: Context',
            target_url='/blog/context',
            minutes_ago=2,
        )
        self._add(
            UserActivity.EVENT_EMAIL_CLICK,
            'Clicked email link',
            minutes_ago=1,
        )

        context = build_activity_context(
            self.member,
            category=ACTIVITY_CATEGORY_LEARNING,
            include_category_counts=True,
        )

        self.assertEqual(context['activity_total'], 1)
        self.assertEqual(len(context['activities']), 1)
        self.assertEqual(
            context['activities'][0]['event_type'],
            UserActivity.EVENT_COURSE_ENROLL,
        )
        self.assertEqual(context['activity_category_counts']['all'], 5)
        self.assertEqual(context['activity_category_counts']['learning'], 1)
        self.assertEqual(context['activity_category_counts']['events'], 1)
        self.assertEqual(context['activity_category_counts']['content'], 1)
        self.assertEqual(context['activity_category_counts']['account'], 1)
        self.assertEqual(context['activity_category_counts']['comms'], 1)

    def test_safe_links_are_public_paths_without_querystrings(self):
        self._add(
            UserActivity.EVENT_COURSE_ENROLL,
            'Legacy Studio course',
            target_url=f'/studio/courses/{self.course.pk}/edit?draft=1',
            object_type='course',
            object_id=self.course.slug,
            minutes_ago=3,
        )
        self._add(
            UserActivity.EVENT_RESOURCE_VIEW,
            'Viewed article',
            target_url='/blog/context?utm_source=email#top',
            minutes_ago=2,
        )
        self._add(
            UserActivity.EVENT_EMAIL_CLICK,
            'Clicked external dashboard',
            target_url='https://dashboard.stripe.com/customers/cus_123',
            minutes_ago=1,
        )

        rows = build_activity_context(self.member)['activities']
        by_label = {row['label']: row for row in rows}
        self.assertEqual(
            by_label['Legacy Studio course']['target_url'],
            '/courses/context-course',
        )
        self.assertEqual(by_label['Viewed article']['target_url'], '/blog/context')
        self.assertEqual(by_label['Clicked external dashboard']['target_url'], '')

    def test_marks_earliest_displayed_payment_as_upgrade_marker(self):
        self._add(
            UserActivity.EVENT_RESOURCE_VIEW,
            'Viewed pre-upgrade article',
            minutes_ago=30,
        )
        old_payment = self._add(
            UserActivity.EVENT_PAYMENT,
            'Payment: Main',
            minutes_ago=20,
        )
        newer_payment = self._add(
            UserActivity.EVENT_PAYMENT,
            'Payment: Premium',
            minutes_ago=5,
        )

        rows = build_activity_context(self.member)['activities']
        marker_ids = [
            row['id'] for row in rows if row['is_upgrade_marker']
        ]

        self.assertEqual(marker_ids, [old_payment.pk])
        self.assertNotIn(newer_payment.pk, marker_ids)

    def test_api_serializer_uses_required_json_shape(self):
        activity = self._add(
            UserActivity.EVENT_EVENT_JOIN,
            'Joined Context Event',
            object_type='event',
            object_id=self.event.slug,
        )
        row = build_activity_context(self.member)['activities'][0]
        serialized = serialize_activity_for_api(row)

        self.assertEqual(serialized['id'], activity.pk)
        self.assertEqual(serialized['event_type'], UserActivity.EVENT_EVENT_JOIN)
        self.assertEqual(serialized['category'], 'events')
        self.assertEqual(serialized['label'], 'Joined Context Event')
        self.assertEqual(serialized['object_type'], 'event')
        self.assertEqual(serialized['object_id'], self.event.slug)
        self.assertEqual(serialized['target_url'], self.event.get_absolute_url())
        self.assertIn('T', serialized['occurred_at'])
