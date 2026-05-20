"""View tests for the staff-only "Edit in Studio" button (issue #667).

The floating button is rendered by ``templates/includes/_studio_edit_button.html``
and included from every public content detail template. The contract:

- Staff users see exactly one button per page, with ``href`` matching the
  model's ``get_studio_edit_url()``.
- Anonymous and non-staff authenticated users see no button at all and
  no leaked ``/studio/`` URL in the HTML body.

These tests hit each in-scope public detail view and assert that
contract.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from content.models import Article, Course, Module, Project, Unit, Workshop
from content.models.workshop import WorkshopPage

User = get_user_model()

STUDIO_BUTTON_TESTID = 'data-testid="studio-edit-button"'


def _staff_user():
    return User.objects.create_user(
        email='staff@test.com', password='pw', is_staff=True,
    )


def _free_user():
    return User.objects.create_user(
        email='free@test.com', password='pw',
    )


@tag('core')
class StudioEditButtonBlogDetailTest(TestCase):
    """Blog detail page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Test Article',
            slug='test-article',
            date=date(2025, 1, 1),
            status='published',
            content_markdown='Body.',
        )

    def test_staff_sees_button_with_studio_edit_url(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/blog/{self.article.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, STUDIO_BUTTON_TESTID, count=1,
        )
        self.assertContains(
            response, f'href="{self.article.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(f'/blog/{self.article.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')

    def test_free_user_does_not_see_button(self):
        _free_user()
        self.client.login(email='free@test.com', password='pw')
        response = self.client.get(f'/blog/{self.article.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonProjectDetailTest(TestCase):
    """Project detail page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Test Project',
            slug='test-project',
            date=date(2025, 1, 1),
            status='published',
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/projects/{self.project.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.project.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(f'/projects/{self.project.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonCourseDetailTest(TestCase):
    """Course detail page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Test Course',
            slug='test-course',
            status='published',
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.course.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonCourseUnitDetailTest(TestCase):
    """Course unit detail page renders the button for staff only.

    The unit page is gated for free users; staff bypass gating because
    ``is_staff`` short-circuits ``can_access``. The button check is
    independent of gating — it depends only on ``request.user.is_staff``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course',
            slug='course-uut',
            status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Mod', slug='mod', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Unit', slug='unit',
            sort_order=1, is_preview=True,
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(
            f'/courses/{self.course.slug}/{self.module.slug}/{self.unit.slug}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.unit.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(
            f'/courses/{self.course.slug}/{self.module.slug}/{self.unit.slug}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonWorkshopDetailTest(TestCase):
    """Workshop detail (landing) page renders the button for staff only."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            title='Test Workshop',
            slug='test-workshop',
            date=date(2025, 1, 1),
            status='published',
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(self.workshop.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.workshop.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(self.workshop.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')


@tag('core')
class StudioEditButtonWorkshopPageDetailTest(TestCase):
    """Workshop tutorial-page detail renders the button for staff only.

    Workshop pages can be gated by ``pages_required_level``; for the
    button check, staff bypass and free users see the gated teaser
    either way. Both branches must NOT include the button for non-staff.
    """

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            title='Workshop',
            slug='workshop-pages',
            date=date(2025, 1, 1),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='Body.',
        )

    def test_staff_sees_button(self):
        _staff_user()
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(
            self.page.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, STUDIO_BUTTON_TESTID, count=1)
        self.assertContains(
            response, f'href="{self.workshop.get_studio_edit_url()}"',
        )

    def test_anonymous_does_not_see_button(self):
        response = self.client.get(
            self.page.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STUDIO_BUTTON_TESTID)
        self.assertNotContains(response, '/studio/')
