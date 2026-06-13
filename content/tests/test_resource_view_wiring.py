"""Tests that `resource_view` is wired into the public content views
(issue #773): authenticated + accessible records; anonymous and gated
teasers do not; course units stay on lesson_open with no double-emit.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from analytics.models import UserActivity
from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import Article, Project, Tutorial
from tests.fixtures import TierSetupMixin

User = get_user_model()


class BlogResourceViewWiringTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', tier=cls.main_tier,
        )
        cls.free = User.objects.create_user(
            email='free@test.com', password='pw', tier=cls.free_tier,
            email_verified=True,
        )
        cls.open_article = Article.objects.create(
            title='Open Read', slug='open-read', date=date(2026, 1, 1),
            published=True, required_level=LEVEL_OPEN,
        )
        cls.gated_article = Article.objects.create(
            title='Gated Read', slug='gated-read', date=date(2026, 1, 1),
            published=True, required_level=LEVEL_MAIN,
        )

    def setUp(self):
        UserActivity.objects.all().delete()

    def _resource_views(self, user, object_id):
        return UserActivity.objects.filter(
            user=user,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            object_id=object_id,
        )

    def test_authenticated_accessible_records_one_row(self):
        self.client.force_login(self.member)
        resp = self.client.get('/blog/open-read')
        self.assertEqual(resp.status_code, 200)
        rows = self._resource_views(self.member, 'open-read')
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.object_type, 'article')
        self.assertTrue(row.label.startswith('Viewed article:'))
        self.assertEqual(row.target_url, '/blog/open-read')

    def test_anonymous_records_nothing(self):
        resp = self.client.get('/blog/open-read')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
                object_id='open-read',
            ).count(),
            0,
        )

    def test_gated_teaser_records_nothing(self):
        # Free member without access sees the Main-gated teaser.
        self.client.force_login(self.free)
        resp = self.client.get('/blog/gated-read')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self._resource_views(self.free, 'gated-read').count(), 0,
        )

    def test_reload_deduped(self):
        self.client.force_login(self.member)
        self.client.get('/blog/open-read')
        self.client.get('/blog/open-read')
        self.assertEqual(
            self._resource_views(self.member, 'open-read').count(), 1,
        )


class ProjectTutorialWiringTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='pt@test.com', password='pw', tier=cls.main_tier,
        )
        cls.project = Project.objects.create(
            title='Build It', slug='build-it', date=date(2026, 1, 1),
            published=True, required_level=LEVEL_OPEN,
        )
        cls.tutorial = Tutorial.objects.create(
            title='How To', slug='how-to', date=date(2026, 1, 1),
            published=True, required_level=LEVEL_OPEN,
        )

    def setUp(self):
        UserActivity.objects.all().delete()

    def test_project_detail_records_resource_view(self):
        self.client.force_login(self.member)
        self.client.get('/projects/build-it')
        row = UserActivity.objects.filter(
            user=self.member,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            object_type='project',
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.object_id, 'build-it')
        self.assertEqual(row.target_url, '/projects/build-it')

    def test_tutorial_detail_records_resource_view(self):
        self.client.force_login(self.member)
        self.client.get('/tutorials/how-to')
        row = UserActivity.objects.filter(
            user=self.member,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            object_type='tutorial',
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.object_id, 'how-to')
        self.assertEqual(row.target_url, '/tutorials/how-to')


class CourseUnitNoDoubleEmitTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from content.models import Course, Module, Unit

        cls.member = User.objects.create_user(
            email='cu@test.com', password='pw', tier=cls.main_tier,
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='LLM Zoomcamp', slug='llm', status='published',
            required_level=LEVEL_OPEN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='m1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Intro', slug='intro', sort_order=1,
            required_level=LEVEL_OPEN,
        )

    def setUp(self):
        UserActivity.objects.all().delete()

    def test_unit_view_emits_lesson_open_not_resource_view(self):
        self.client.force_login(self.member)
        resp = self.client.get('/courses/llm/m1/intro')
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(
            UserActivity.objects.filter(
                user=self.member,
                event_type=UserActivity.EVENT_LESSON_OPEN,
            ).count(),
            1,
        )
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.member,
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
            ).count(),
            0,
        )
