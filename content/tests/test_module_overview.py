"""Tests for the module overview page (issue #222).

Module READMEs used to render as a sibling Unit row, which duplicated the
module title and bloated lesson counts. They now populate
``Module.overview`` and render at the bare module URL
``/courses/<course>/<module>/``.

Covers:
- ``Module.overview_html`` is generated from ``overview`` markdown on save
- The leading H1 in the overview is stripped when it duplicates the module
  title (via ``strip_leading_title_h1``)
- ``/courses/<course>/<module>/`` renders the overview + lesson list
- Module without an overview still renders the lesson list
- Lesson list excludes legacy README-as-Unit rows (defensive)
- ``/courses/<course>/<module>/readme`` permanently redirects to the
  overview URL
- ``Course.total_units()`` ignores legacy README-as-Unit rows
- ``Module.get_absolute_url()``
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Course, Module, Unit, UserCourseProgress

User = get_user_model()


# ---------------------------------------------------------------------------
# Module model
# ---------------------------------------------------------------------------


class ModuleOverviewModelTest(TestCase):
    """Module.overview / overview_html / get_absolute_url."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Course', slug='course')

    def test_overview_html_rendered_on_save(self):
        module = Module.objects.create(
            course=self.course, title='Module', slug='module', sort_order=1,
            overview='Welcome to **the** module.\n',
        )
        self.assertIn('<strong>the</strong>', module.overview_html)
        self.assertIn('Welcome to', module.overview_html)

    def test_overview_h1_matching_title_is_stripped(self):
        """A leading ``# Module Title`` is dropped to avoid duplicate headings."""
        module = Module.objects.create(
            course=self.course, title='Intro', slug='intro', sort_order=1,
            overview='# Intro\n\nReal body.\n',
        )
        self.assertNotIn('<h1>Intro</h1>', module.overview_html)
        self.assertIn('Real body.', module.overview_html)

    def test_overview_h1_distinct_from_title_is_kept(self):
        module = Module.objects.create(
            course=self.course, title='Intro', slug='intro2', sort_order=1,
            overview='# Welcome\n\nReal body.\n',
        )
        self.assertIn('<h1>Welcome</h1>', module.overview_html)

    def test_empty_overview_clears_overview_html(self):
        module = Module.objects.create(
            course=self.course, title='M', slug='m-empty', sort_order=1,
            overview='# Hi\n\nBody.\n',
        )
        self.assertNotEqual(module.overview_html, '')
        # Clearing overview must clear overview_html on the next save.
        module.overview = ''
        module.save()
        self.assertEqual(module.overview_html, '')

    def test_get_absolute_url(self):
        module = Module.objects.create(
            course=self.course, title='M', slug='m-url', sort_order=1,
        )
        # No trailing slash: the project uses RemoveTrailingSlashMiddleware,
        # so trailing-slash URLs would be redirected away.
        self.assertEqual(module.get_absolute_url(), '/courses/course/m-url')


# ---------------------------------------------------------------------------
# Module overview view
# ---------------------------------------------------------------------------


class ModuleOverviewViewTest(TestCase):
    """``GET /courses/<course>/<module>/`` renders overview + lesson list."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Python Course', slug='python-course', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Fundamentals', slug='fundamentals',
            sort_order=1,
            overview='# Fundamentals\n\nLearn the basics here.\n',
        )
        cls.unit_a = Unit.objects.create(
            module=cls.module, title='Why Python', slug='why', sort_order=1,
        )
        cls.unit_b = Unit.objects.create(
            module=cls.module, title='Setup', slug='setup', sort_order=2,
        )

    def test_overview_page_returns_200(self):
        response = self.client.get('/courses/python-course/fundamentals')
        self.assertEqual(response.status_code, 200)

    def test_overview_page_uses_module_overview_template(self):
        response = self.client.get('/courses/python-course/fundamentals')
        self.assertTemplateUsed(response, 'content/module_overview.html')

    def test_overview_page_renders_overview_html(self):
        response = self.client.get('/courses/python-course/fundamentals')
        self.assertContains(response, 'Learn the basics here.')

    def test_overview_page_does_not_duplicate_module_title(self):
        """The page heading is the module title — overview H1 must be stripped."""
        response = self.client.get('/courses/python-course/fundamentals')
        # The page heading appears once via the template; the overview
        # body's `# Fundamentals` H1 was stripped on save.
        self.assertNotContains(response, '<h1>Fundamentals</h1>')

    def test_overview_page_lists_units_separately_from_overview(self):
        """Lesson list is a distinct section, not interleaved with overview."""
        response = self.client.get('/courses/python-course/fundamentals')
        # Both real units appear as lesson rows.
        self.assertContains(response, 'Why Python')
        self.assertContains(response, 'Setup')
        # Lesson list section exists.
        self.assertContains(response, 'data-testid="module-lesson-list"')
        # Lesson links point at the unit detail URLs (not the bare module).
        self.assertContains(
            response, 'href="/courses/python-course/fundamentals/why"',
        )
        # Crucially, no /readme lesson row leaks through.
        self.assertNotContains(
            response, '/courses/python-course/fundamentals/readme',
        )

    def test_overview_page_breadcrumb_links_back_to_course(self):
        response = self.client.get('/courses/python-course/fundamentals')
        self.assertContains(response, '/courses/python-course')
        self.assertContains(response, 'Python Course')

    def test_module_without_overview_still_renders_lesson_list(self):
        """Falls back to lesson-list-only layout when overview is empty."""
        bare = Module.objects.create(
            course=self.course, title='Bare', slug='bare', sort_order=2,
        )
        Unit.objects.create(
            module=bare,
            title='Lesson One', slug='lesson-one', sort_order=1,
        )
        response = self.client.get('/courses/python-course/bare')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="module-overview"')
        self.assertContains(response, 'Lesson One')
        self.assertContains(response, 'data-testid="module-lesson-list"')

    def test_unknown_course_returns_404(self):
        response = self.client.get('/courses/no-such-course/fundamentals')
        self.assertEqual(response.status_code, 404)

    def test_unknown_module_returns_404(self):
        response = self.client.get('/courses/python-course/no-such-module')
        self.assertEqual(response.status_code, 404)

    def test_draft_course_returns_404(self):
        draft = Course.objects.create(
            title='Draft', slug='draft-course', status='draft',
        )
        Module.objects.create(
            course=draft, title='M', slug='m', sort_order=1,
        )
        response = self.client.get('/courses/draft-course/m')
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Old /readme URL redirect
# ---------------------------------------------------------------------------


class ModuleReadmeRedirectTest(TestCase):
    """Old ``/<course>/<module>/readme`` URLs 301-redirect to the overview."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Python', slug='python', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Intro', slug='intro', sort_order=1,
        )

    def test_readme_url_permanently_redirects_to_module_overview(self):
        response = self.client.get(
            '/courses/python/intro/readme', follow=False,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/courses/python/intro')

    def test_redirect_works_even_when_overview_is_empty(self):
        # No overview content; the redirect still applies — readers will land
        # on the lesson-list-only page.
        response = self.client.get(
            '/courses/python/intro/readme', follow=False,
        )
        self.assertEqual(response.status_code, 301)

    def test_readme_url_404s_for_unknown_module(self):
        response = self.client.get(
            '/courses/python/no-such-module/readme', follow=False,
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Lesson counts ignore legacy README-as-Unit rows
# ---------------------------------------------------------------------------


class LessonCountIgnoresLegacyReadmeUnitTest(TestCase):
    """``Course.total_units()`` excludes legacy ``slug='readme', sort_order=-1``
    rows so progress percentages and "X lessons" labels don't drift after
    the backfill migration runs.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='ml@example.com')
        cls.course = Course.objects.create(
            title='C', slug='c-readme-count', status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='M', slug='m', sort_order=1,
        )
        # A real lesson...
        cls.real_unit = Unit.objects.create(
            module=cls.module, title='Real', slug='real', sort_order=1,
        )
        # ...and a legacy README-as-Unit row that snuck in (e.g. created
        # by a sync that ran before this code rolled out, against a DB
        # that hasn't been backfilled yet).
        cls.legacy_readme = Unit.objects.create(
            module=cls.module, title='M', slug='readme', sort_order=-1,
        )

    def test_total_units_excludes_legacy_readme_unit(self):
        self.assertEqual(self.course.total_units(), 1)

    def test_completed_units_excludes_legacy_readme_unit(self):
        # Mark both as completed; only the real one should count.
        UserCourseProgress.objects.create(
            user=self.user, unit=self.real_unit,
            completed_at=__import__('django.utils.timezone', fromlist=['']).now(),
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.legacy_readme,
            completed_at=__import__('django.utils.timezone', fromlist=['']).now(),
        )
        self.assertEqual(self.course.completed_units(self.user), 1)

    def test_get_next_unit_for_skips_legacy_readme_unit(self):
        # With nothing completed, "next" must be the real unit, not the
        # legacy README.
        nxt = self.course.get_next_unit_for(self.user)
        self.assertEqual(nxt.pk, self.real_unit.pk)
