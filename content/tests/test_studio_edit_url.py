"""Tests for ``get_studio_edit_url`` on content models (issue #667).

Each in-scope content model exposes ``get_studio_edit_url`` returning the
Studio editor URL for that record. The button partial uses this method to
decide where the staff "Edit in Studio" link points. These tests assert
the exact string for every model so a Studio URL rename can't silently
break the button.
"""

from datetime import date

from django.test import TestCase, tag

from content.models import Article, Course, Module, Project, Unit, Workshop


@tag('core')
class ContentModelStudioEditUrlTest(TestCase):
    """Every in-scope content model returns the canonical Studio editor URL.

    The URLs are hard-coded f-strings (matching the ``get_absolute_url``
    pattern in the same models). If the Studio URL conf is reshuffled,
    these assertions force the model methods to be updated in lockstep
    so the public-page button keeps pointing at a real editor.
    """

    def test_article_studio_edit_url(self):
        article = Article.objects.create(
            title='Test Article',
            slug='test-article',
            date=date(2025, 1, 1),
        )
        self.assertEqual(
            article.get_studio_edit_url(),
            f'/studio/articles/{article.pk}/edit',
        )

    def test_project_studio_edit_url(self):
        project = Project.objects.create(
            title='Test Project',
            slug='test-project',
            date=date(2025, 1, 1),
        )
        self.assertEqual(
            project.get_studio_edit_url(),
            f'/studio/projects/{project.pk}/review',
        )

    def test_course_studio_edit_url(self):
        course = Course.objects.create(
            title='Test Course',
            slug='test-course',
            status='published',
        )
        self.assertEqual(
            course.get_studio_edit_url(),
            f'/studio/courses/{course.pk}/edit',
        )

    def test_unit_studio_edit_url(self):
        course = Course.objects.create(
            title='Course',
            slug='course-x',
            status='published',
        )
        module = Module.objects.create(
            course=course,
            title='Module',
            slug='module-x',
            sort_order=1,
        )
        unit = Unit.objects.create(
            module=module,
            title='Unit',
            slug='unit-x',
            sort_order=1,
        )
        self.assertEqual(
            unit.get_studio_edit_url(),
            f'/studio/units/{unit.pk}/edit',
        )

    def test_workshop_studio_edit_url(self):
        workshop = Workshop.objects.create(
            title='Test Workshop',
            slug='test-workshop',
            date=date(2025, 1, 1),
        )
        self.assertEqual(
            workshop.get_studio_edit_url(),
            f'/studio/workshops/{workshop.pk}/edit',
        )
