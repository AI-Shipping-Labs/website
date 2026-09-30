"""Enrollment-count badge on the catalog card, course page and course Home."""

import datetime
import re

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import User
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Enrollment,
    Module,
    Unit,
)
from content.services.enrollment import active_enrollment_counts


def _badge_texts(response, testid):
    """Return the visible label of every badge carrying ``testid``."""
    html = response.content.decode()
    pattern = rf'<span[^>]*data-testid="{testid}"[^>]*>(.*?)</span>'
    return [
        ' '.join(re.sub(r'<[^>]+>', ' ', body).split())
        for body in re.findall(pattern, html, flags=re.S)
    ]


def _learners(prefix, count):
    return [
        User.objects.create_user(
            email=f'{prefix}{i}@example.com', password='testpass',
            email_verified=True,
        )
        for i in range(count)
    ]


def _course(slug, title):
    course = Course.objects.create(
        title=title, slug=slug, status='published', required_level=0,
    )
    module = Module.objects.create(
        course=course, title='Mod', slug=f'{slug}-mod', sort_order=0,
    )
    Unit.objects.create(module=module, title='Lesson', slug=f'{slug}-u1', sort_order=0)
    return course


@tag('core')
class ActiveEnrollmentCountsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = _course('count-a', 'Count A')
        cls.other = _course('count-b', 'Count B')
        learners = _learners('count-svc-', 3)
        for learner in learners:
            Enrollment.objects.create(user=learner, course=cls.course)
        Enrollment.objects.filter(user=learners[0]).update(
            unenrolled_at=timezone.now(),
        )

    def test_counts_only_active_enrollments_and_omits_empty_courses(self):
        counts = active_enrollment_counts([self.course.pk, self.other.pk])
        self.assertEqual(counts, {self.course.pk: 2})


@tag('core')
class EnrollmentCountBadgeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.popular = _course('popular-course', 'Popular course')
        cls.empty = _course('empty-course', 'Empty course')
        cls.learners = _learners('badge-', 3)
        for learner in cls.learners:
            Enrollment.objects.create(user=learner, course=cls.popular)
        # A soft-deleted enrollment never counts.
        leaver = User.objects.create_user(
            email='badge-leaver@example.com', password='testpass',
        )
        Enrollment.objects.create(
            user=leaver, course=cls.popular, unenrolled_at=timezone.now(),
        )

    def test_catalog_card_shows_count_and_hides_it_at_zero(self):
        response = self.client.get('/courses')
        self.assertEqual(
            _badge_texts(response, 'course-enrollment-count-badge'),
            ['3 enrolled'],
        )

    def test_course_page_header_shows_count(self):
        response = self.client.get('/courses/popular-course')
        self.assertEqual(
            _badge_texts(response, 'course-detail-enrollment-count-badge'),
            ['3 enrolled'],
        )

    def test_course_page_header_hides_badge_at_zero(self):
        response = self.client.get('/courses/empty-course')
        self.assertContains(response, 'Empty course')
        self.assertEqual(
            _badge_texts(response, 'course-detail-enrollment-count-badge'), [],
        )


@tag('core')
class CourseHomeEnrollmentCountBadgeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = _course('home-count', 'Home count course')
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='c4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=30),
        )
        cls.learners = _learners('home-count-', 4)
        # Two cohort members, four course enrollments: the dated cohort
        # label shows the cohort's own member count.
        for learner in cls.learners:
            Enrollment.objects.create(user=learner, course=cls.course)
        for learner in cls.learners[:2]:
            CohortEnrollment.objects.create(cohort=cls.cohort, user=learner)

    def test_dated_cohort_label_shows_cohort_member_count(self):
        self.client.force_login(self.learners[0])
        response = self.client.get('/courses/home-count/home')
        self.assertEqual(
            _badge_texts(response, 'course-home-enrollment-count-badge'),
            ['2 enrolled'],
        )

    def test_self_paced_label_shows_course_count(self):
        self.client.force_login(self.learners[3])
        course = _course('self-paced-count', 'Self-paced count course')
        for learner in self.learners:
            Enrollment.objects.create(user=learner, course=course)
        response = self.client.get('/courses/self-paced-count/home')
        self.assertEqual(
            _badge_texts(response, 'course-home-enrollment-count-badge'),
            ['4 enrolled'],
        )
