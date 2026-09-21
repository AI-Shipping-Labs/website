"""Cohort selection and deadline isolation on the syllabus."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as django_timezone

from content.models import Cohort, CohortEnrollment, Course, Homework, Module, Unit
from content.models.peer_review import CourseProject


class CourseScheduleDisplayTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='ai-buildcamp', status='published', required_level=0,
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Foundation', slug='foundation', sort_order=1,
            available_after_days=0,
        )
        cls.topic = Module.objects.create(
            course=cls.course, parent=cls.week, title='First topic',
            slug='first-topic', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.topic, title='Homework 1', slug='homework-1',
            sort_order=1, kind='homework', content_id=uuid4(),
        )
        cls.missing_unit = Unit.objects.create(
            module=cls.topic, title='Homework without date',
            slug='homework-without-date', sort_order=2, kind='homework',
            content_id=uuid4(),
        )
        cls.c4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=date(2026, 9, 21), end_date=date(2026, 11, 22),
        )
        cls.c5 = Cohort.objects.create(
            course=cls.course, name='Cohort 5', external_key='5',
            start_date=date(2027, 1, 1), end_date=date(2027, 3, 1),
        )
        cls.due4 = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)
        cls.due5 = datetime(2027, 2, 1, 18, tzinfo=timezone.utc)
        Homework.objects.create(
            cohort=cls.c4, content_id=cls.unit.content_id, slug='hw1',
            title='Homework 1', due_date=cls.due4,
        )
        Homework.objects.create(
            cohort=cls.c5, content_id=cls.unit.content_id, slug='hw1',
            title='Homework 1', due_date=cls.due5,
        )
        CourseProject.objects.create(
            course=cls.course, cohort=cls.c4, module=cls.topic,
            slug='attempt-4', title='Attempt 4',
            submission_due_at=cls.due4, review_due_at=cls.due4.replace(day=5),
        )
        CourseProject.objects.create(
            course=cls.course, cohort=cls.c5, module=cls.topic,
            slug='attempt-5', title='Attempt 5',
            submission_due_at=cls.due5, review_due_at=cls.due5.replace(day=5),
        )
        CourseProject.objects.create(
            course=cls.course, module=cls.topic,
            slug='open-attempt', title='Open Attempt',
            submission_due_at=cls.due4, review_due_at=cls.due4.replace(day=5),
        )
        cls.learner = get_user_model().objects.create_user(
            email='cohort4@example.com', password='pw',
        )
        CohortEnrollment.objects.create(user=cls.learner, cohort=cls.c4)

    def test_public_query_selects_only_valid_active_course_cohort(self):
        response = self.client.get('/courses/ai-buildcamp?cohort=5')
        self.assertEqual(response.context['schedule_cohort'], self.c5)
        self.assertEqual(response.context['unit_deadlines'][self.unit.pk], self.due5)
        self.assertContains(response, 'Cohort 5 schedule')
        self.assertContains(response, 'Deadline to be announced')
        self.assertContains(response, 'data-testid="syllabus-project-preview"')
        self.assertNotContains(response, 'Attempt 4')
        submit_url = reverse('course_project_submit', kwargs={
            'slug': self.course.slug, 'attempt_slug': 'attempt-5',
        })
        self.assertNotContains(response, f'href="{submit_url}"')
        global_submit_url = reverse('course_project_submit', kwargs={
            'slug': self.course.slug, 'attempt_slug': 'open-attempt',
        })
        self.assertNotContains(response, f'href="{global_submit_url}"')

        invalid = self.client.get('/courses/ai-buildcamp?cohort=missing')
        self.assertIsNone(invalid.context['schedule_cohort'])
        self.assertNotContains(invalid, 'Feb 1, 2027 18:00')

    def test_enrolled_schedule_and_deadlines_reach_parent_summary(self):
        self.client.force_login(self.learner)
        response = self.client.get('/courses/ai-buildcamp')
        self.assertEqual(response.context['schedule_cohort'], self.c4)
        self.assertFalse(response.context['schedule_is_preview'])
        self.assertEqual(response.context['unit_deadlines'][self.unit.pk], self.due4)
        self.assertEqual(response.context['module_deadline_summaries'][self.week.pk]['count'], 5)
        self.assertNotContains(response, 'Feb 1, 2027 18:00')
        self.assertContains(response, 'Oct 1, 2026 20:00 Europe/Berlin')

    def test_dual_enrollment_prefers_current_then_requested_owned_cohort(self):
        today = django_timezone.localdate()
        self.c4.start_date = today - timedelta(days=7)
        self.c4.end_date = today + timedelta(days=7)
        self.c4.save(update_fields=['start_date', 'end_date'])
        older = Cohort.objects.create(
            course=self.course, name='Cohort 3', external_key='3',
            start_date=today - timedelta(days=180),
            end_date=today - timedelta(days=120),
        )
        CohortEnrollment.objects.create(user=self.learner, cohort=older)
        self.client.force_login(self.learner)
        current = self.client.get('/courses/ai-buildcamp')
        self.assertEqual(current.context['schedule_cohort'], self.c4)
        chosen = self.client.get('/courses/ai-buildcamp?cohort=3')
        self.assertEqual(chosen.context['schedule_cohort'], older)

    def test_unavailable_explicit_key_does_not_fall_back_to_enrollment(self):
        other_course = Course.objects.create(
            title='Other', slug='other-schedule-course', status='published',
        )
        Cohort.objects.create(
            course=other_course, name='Other Cohort 6', external_key='6',
            start_date=date(2026, 9, 21), end_date=date(2026, 11, 22),
        )
        self.client.force_login(self.learner)
        for key in ('5', '6', 'missing'):
            with self.subTest(key=key):
                response = self.client.get(f'/courses/ai-buildcamp?cohort={key}')
                self.assertIsNone(response.context['schedule_cohort'])
                self.assertEqual(response.context['unit_deadlines'], {})

    def test_dual_enrollment_uses_nearest_upcoming_then_latest_past(self):
        today = django_timezone.localdate()
        self.c4.start_date = today - timedelta(days=90)
        self.c4.end_date = today - timedelta(days=60)
        self.c4.save(update_fields=['start_date', 'end_date'])
        self.c5.start_date = today + timedelta(days=14)
        self.c5.end_date = today + timedelta(days=70)
        self.c5.save(update_fields=['start_date', 'end_date'])
        CohortEnrollment.objects.create(user=self.learner, cohort=self.c5)
        self.client.force_login(self.learner)
        upcoming = self.client.get('/courses/ai-buildcamp')
        self.assertEqual(upcoming.context['schedule_cohort'], self.c5)

        self.c5.start_date = today - timedelta(days=50)
        self.c5.end_date = today - timedelta(days=20)
        self.c5.save(update_fields=['start_date', 'end_date'])
        past = self.client.get('/courses/ai-buildcamp')
        self.assertEqual(past.context['schedule_cohort'], self.c5)

    def test_single_active_buildcamp_cohort_is_public_default(self):
        self.c5.is_active = False
        self.c5.save(update_fields=['is_active'])
        response = self.client.get('/courses/ai-buildcamp')
        self.assertEqual(response.context['schedule_cohort'], self.c4)
        self.assertTrue(response.context['schedule_is_preview'])

    def test_staff_can_preview_another_cohort_when_enrolled(self):
        self.learner.is_staff = True
        self.learner.save(update_fields=['is_staff'])
        self.client.force_login(self.learner)
        response = self.client.get('/courses/ai-buildcamp?cohort=5')
        self.assertEqual(response.context['schedule_cohort'], self.c5)
        self.assertTrue(response.context['schedule_is_preview'])
