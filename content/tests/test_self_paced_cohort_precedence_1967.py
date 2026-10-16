"""Default cohort selection for returning self-paced alumni (#1967)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course
from content.services.course_schedule import select_display_cohort

User = get_user_model()


class SelfPacedCohortPrecedenceTest(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.course = Course.objects.create(
            title='Returning learner course', slug='returning-self-paced-1967',
            status='published', required_level=0,
        )
        self.alumnus = Cohort.objects.create(
            course=self.course, name='Cohort 2', external_key='cohort-2',
            start_date=today - timedelta(days=90),
            end_date=today - timedelta(days=30),
        )
        self.self_paced = Cohort.objects.create(
            course=self.course, name='Self-paced', external_key='self-paced',
            mode='self_paced', is_active=True,
        )
        self.user = User.objects.create_user(
            email='returning-self-paced-1967@test.com', password='pw',
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self.alumnus)
        CohortEnrollment.objects.create(user=self.user, cohort=self.self_paced)
        self.client.force_login(self.user)

    def test_self_paced_default_wins_over_ended_dated_history(self):
        cohort, is_preview = select_display_cohort(self.course, self.user)
        response = self.client.get(f'/courses/{self.course.slug}/home')

        self.assertEqual((cohort, is_preview), (self.self_paced, False))
        self.assertEqual(response.context['cohort'], self.self_paced)
        self.assertTrue(response.context['self_paced_view'])
        self.assertNotContains(response, '/home/sessions')

    def test_owned_historical_cohort_remains_explicitly_selectable(self):
        response = self.client.get(
            f'/courses/{self.course.slug}/home?cohort=cohort-2',
        )

        self.assertEqual(response.context['cohort'], self.alumnus)
        self.assertFalse(response.context['self_paced_view'])

    def test_current_dated_membership_keeps_default_precedence(self):
        today = timezone.localdate()
        self.alumnus.start_date = today - timedelta(days=2)
        self.alumnus.end_date = today + timedelta(days=20)
        self.alumnus.save(update_fields=['start_date', 'end_date'])

        cohort, is_preview = select_display_cohort(self.course, self.user)
        response = self.client.get(f'/courses/{self.course.slug}/home')

        self.assertEqual((cohort, is_preview), (self.alumnus, False))
        self.assertEqual(response.context['cohort'], self.alumnus)
        self.assertFalse(response.context['self_paced_view'])
