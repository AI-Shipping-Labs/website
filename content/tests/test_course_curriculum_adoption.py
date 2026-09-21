"""Gating tests: public course pages are backed by community-base curriculum."""

import os
import tempfile
import uuid

from community_base.curriculum.models import Course as CurriculumCourse
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from content.models import Course, CourseAccess, UserCourseProgress
from content.tests.factories import make_course_with_units
from tests.fixtures import TierSetupMixin, set_membership


def _make_user(email, tier):
    user = User.objects.create_user(
        email=email, password='testpass', email_verified=True,
    )
    set_membership(user, tier=tier)
    return user


class CurriculumBackedCoursePagesTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_user = _make_user('free-curriculum@example.com', cls.free_tier)
        cls.premium_user = _make_user('premium-curriculum@example.com', cls.premium_tier)
        built = make_course_with_units(
            slug='curriculum-gated',
            title='Curriculum Gated Course',
            status='published',
            required_level=30,
            default_unit_required_level=30,
            modules=[
                {
                    'title': 'Week 1',
                    'slug': 'week-1',
                    'sort_order': 1,
                    'units': [
                        {
                            'title': 'Lesson One',
                            'slug': 'lesson-one',
                            'sort_order': 1,
                            'body': 'Secret lesson body',
                            'required_level': 30,
                        },
                    ],
                },
            ],
        )
        cls.course = built.course
        cls.unit = built.units[0]

    def test_rows_are_package_curriculum_models(self):
        self.assertIsInstance(self.course, CurriculumCourse)
        self.assertEqual(Course.objects.get(slug='curriculum-gated').pk, self.course.pk)
        self.assertEqual(
            CurriculumCourse.objects.filter(slug='curriculum-gated').count(),
            1,
        )

    def test_catalog_names_the_course(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'Curriculum Gated Course', status_code=200)

    def test_detail_page_shows_the_course(self):
        response = self.client.get('/courses/curriculum-gated')
        self.assertContains(response, 'Curriculum Gated Course', status_code=200)
        self.assertContains(response, 'Week 1')
        self.assertContains(response, 'Lesson One')

    def test_unit_url_shape_still_resolves(self):
        url = self.unit.get_absolute_url()
        self.assertEqual(url, '/courses/curriculum-gated/week-1/lesson-one')
        self.client.force_login(self.premium_user)
        response = self.client.get(url)
        self.assertContains(response, 'Secret lesson body', status_code=200)

    def test_gated_unit_locked_for_free_user(self):
        self.assertEqual(self.course.required_level, 30)
        self.assertEqual(self.unit.effective_required_level, 30)
        self.client.force_login(self.free_user)
        response = self.client.get(self.unit.get_absolute_url())
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'data-testid="course-unit-body"', status_code=403)

    def test_grant_unlocks_unit_body(self):
        CourseAccess.objects.create(
            user=self.free_user,
            course=self.course,
            access_type='granted',
        )
        self.client.force_login(self.free_user)
        response = self.client.get(self.unit.get_absolute_url())
        self.assertContains(response, 'Secret lesson body', status_code=200)

    def test_complete_then_uncomplete_persists(self):
        self.client.force_login(self.premium_user)
        complete_url = reverse(
            'api_course_unit_complete',
            kwargs={'slug': self.course.slug, 'unit_id': self.unit.pk},
        )
        self.client.post(complete_url)
        progress = UserCourseProgress.objects.get(
            user=self.premium_user, unit=self.unit,
        )
        self.assertIsNotNone(progress.completed_at)
        self.client.post(complete_url)
        self.assertFalse(
            UserCourseProgress.objects.filter(
                user=self.premium_user, unit=self.unit,
            ).exists(),
        )


class CurriculumSyncDoesNotDuplicateCourseTest(TestCase):
    def setUp(self):
        from integrations.models import ContentSource

        self.source = ContentSource.objects.create(
            repo_name='test-org/curriculum-once',
        )
        self.temp_dir = tempfile.mkdtemp()
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write(
                'title: Once Course\n'
                'slug: once-course\n'
                f'content_id: "{uuid.uuid4()}"\n'
            )
        module_dir = os.path.join(self.temp_dir, '01-intro')
        os.makedirs(module_dir)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: Intro\nslug: intro\n')
        with open(os.path.join(module_dir, '01-hello.md'), 'w') as f:
            f.write(
                '---\n'
                'title: Hello\n'
                'slug: hello\n'
                f'content_id: "{uuid.uuid4()}"\n'
                '---\n'
                'Hello body.\n'
            )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resync_keeps_a_single_curriculum_course_row(self):
        from integrations.services.github import sync_content_source

        sync_content_source(self.source, repo_dir=self.temp_dir)
        sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(Course.objects.filter(slug='once-course').count(), 1)
        self.assertEqual(
            CurriculumCourse.objects.filter(slug='once-course').count(),
            1,
        )
