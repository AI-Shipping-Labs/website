"""Studio distinguishes actual self-paced cohorts from legacy rows."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Cohort, Course, CourseProject
from studio.templatetags.studio_filters import STATUS_BADGE_CLASSES


class SelfPacedPeerReviewStudioTest(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user(
            email='staff-1967@test.com', is_staff=True,
        )
        self.course = Course.objects.create(
            title='Rolling', slug='rolling-studio-1967',
            peer_review_enabled=True,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Rolling self-paced',
            mode='self_paced', is_active=True,
        )
        CourseProject.objects.create(
            course=self.course, cohort=self.cohort,
            slug='project', title='Project',
            submission_due_at=None,
            review_due_at=None,
        )
        self.client.force_login(staff)

    def test_actual_self_paced_cohort_has_truthful_schedule_copy(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )

        group = response.context['cohort_groups'][0]
        self.assertTrue(group['is_self_paced'])
        project = response.context['course_projects'][0]
        self.assertTrue(project.is_self_paced)
        self.assertIsNone(group['start_date'])
        self.assertIsNone(group['end_date'])

    def test_inactive_cohort_uses_neutral_shared_status_badge(self):
        self.cohort.is_active = False
        self.cohort.save(update_fields=['is_active'])

        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )

        group = response.context['cohort_groups'][0]
        self.assertFalse(group['is_active'])
        self.assertEqual(
            STATUS_BADGE_CLASSES['archived'],
            STATUS_BADGE_CLASSES['completed'],
        )
