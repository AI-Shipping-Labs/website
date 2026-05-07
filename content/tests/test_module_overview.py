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
- ``Module.get_absolute_url()``
"""

from django.test import TestCase

from content.models import Course, Module, Unit

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


