"""Issue #517: Workshop and course reader mobile progress bar.

Covers (Django HTML-rendering layer):

- Workshop tutorial reader exposes the new mobile progress bar partial
  (``data-testid="reader-mobile-progress-bar"``) above the breadcrumb,
  the toggle keeps the shared ``id="sidebar-toggle-btn"`` /
  ``aria-controls="sidebar-nav"`` contract, and the position text reads
  ``Page N of M``.
- Course unit reader exposes the same partial with ``Lesson N of M``
  text relative to the flat module-ordered unit list across the whole
  course.
- The progress fill bar renders only when the visitor is authenticated
  (anonymous gets the position text only).
- A gated workshop page does NOT render the mobile progress bar — its
  paywall is the only chrome shown alongside the H1 / breadcrumb.
- ``reader_mobile_label``, ``reader_progress_*`` are present in the view
  context regardless of auth state (anonymous gets
  ``reader_progress_completed = 0``).
- The mobile toggle button has been lifted out of the persistent
  sidebar `<aside>` (regression guard for the layout fix that motivated
  this issue: the old toggle sat below the body on mobile).
- The two reader templates contain no leaked multi-line ``{# ... #}``
  comments (per ``feedback_django_comment_leak.md``).

Live click / drawer-open / completion-after-reload flows are covered by
``playwright_tests/test_workshop_reader_mobile_progress_517.py``.
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import (
    Course,
    Module,
    Unit,
    Workshop,
    WorkshopPage,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_workshop(slug, title, pages_required_level=LEVEL_OPEN, page_data=None):
    """Build a published workshop with `page_data = [(slug, title, body), ...]`."""
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        status='published',
        date=date(2026, 4, 21),
        landing_required_level=LEVEL_OPEN,
        pages_required_level=pages_required_level,
        recording_required_level=LEVEL_MAIN,
        description='Description',
    )
    pages = page_data or [
        ('intro', 'Intro', 'body 1'),
        ('setup', 'Setup', 'body 2'),
        ('regional-setup', 'Regional Setup', 'body 3'),
        ('cli', 'CLI', 'body 4'),
        ('verify', 'Verify', 'body 5'),
    ]
    for i, (s, t, body) in enumerate(pages, start=1):
        WorkshopPage.objects.create(
            workshop=workshop, slug=s, title=t, sort_order=i, body=body,
        )
    return workshop


def _make_course_with_units(slug='intro-to-llms', module_unit_counts=(4, 3, 2),
                            required_level=LEVEL_OPEN):
    """Build a published course with `module_unit_counts` units per module."""
    course = Course.objects.create(
        title='Course Title',
        slug=slug,
        status='published',
        required_level=required_level,
    )
    for m_idx, count in enumerate(module_unit_counts, start=1):
        module = Module.objects.create(
            course=course, title=f'Module {m_idx}',
            slug=f'module-{m_idx}', sort_order=m_idx,
        )
        for u_idx in range(1, count + 1):
            Unit.objects.create(
                module=module,
                title=f'Module {m_idx} Unit {u_idx}',
                slug=f'm{m_idx}-u{u_idx}',
                sort_order=u_idx,
                body=f'body m{m_idx} u{u_idx}',
            )
    return course


class WorkshopMobileProgressBarContextTest(TierSetupMixin, TestCase):
    """The workshop tutorial view exposes the reader_progress_* context."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='regional-setup', title='Regional Setup',
            pages_required_level=LEVEL_OPEN,
        )

    def test_anonymous_visitor_gets_progress_text_without_completion(self):
        """Anonymous get position text + drawer toggle but no fill bar."""
        url = '/workshops/regional-setup/tutorial/regional-setup'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_current'], 3)
        self.assertEqual(response.context['reader_progress_total'], 5)
        self.assertEqual(response.context['reader_progress_completed'], 0)
        self.assertEqual(response.context['reader_progress_kind'], 'page')
        self.assertEqual(
            response.context['reader_mobile_label'], 'Workshop Navigation',
        )
        self.assertContains(
            response, 'data-testid="reader-mobile-progress-bar"',
        )
        self.assertContains(response, 'Page 3 of 5')
        self.assertContains(
            response, 'data-testid="reader-mobile-drawer-toggle"',
        )
        # No fill bar for anonymous visitor.
        self.assertNotContains(
            response, 'data-testid="reader-mobile-progress-fill"',
        )

    def test_authenticated_user_gets_progress_fill_bar(self):
        """Logged-in user sees the fill bar with the right testid."""
        user = User.objects.create_user(
            email='main@test.com', password='x', tier=self.main_tier,
        )
        client = Client()
        client.force_login(user)
        url = '/workshops/regional-setup/tutorial/regional-setup'
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        # Fill bar present for an authenticated visitor.
        self.assertContains(
            response, 'data-testid="reader-mobile-progress-fill"',
        )
        self.assertContains(response, 'Page 3 of 5')

    def test_first_page_progress_is_one(self):
        url = '/workshops/regional-setup/tutorial/intro'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_current'], 1)
        self.assertContains(response, 'Page 1 of 5')

    def test_last_page_progress_matches_total(self):
        url = '/workshops/regional-setup/tutorial/verify'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_current'], 5)
        self.assertContains(response, 'Page 5 of 5')

    def test_single_page_workshop_renders_one_of_one(self):
        Workshop.objects.filter(slug='regional-setup').delete()
        _make_workshop(
            slug='only-one', title='Only One',
            pages_required_level=LEVEL_OPEN,
            page_data=[('only', 'Only Page', 'body')],
        )
        response = self.client.get('/workshops/only-one/tutorial/only')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Page 1 of 1')


class WorkshopMobileProgressBarHiddenWhenGatedTest(TierSetupMixin, TestCase):
    """Gated workshop pages do NOT render the mobile progress bar."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='main-only', title='Main Only',
            pages_required_level=LEVEL_MAIN,
            page_data=[
                ('intro', 'Intro', 'gated body'),
                ('next', 'Next', 'next body'),
            ],
        )

    def test_free_user_on_gated_page_does_not_see_progress_bar(self):
        free_user = User.objects.create_user(
            email='free@test.com', password='x', tier=self.free_tier,
            email_verified=True,
        )
        client = Client()
        client.force_login(free_user)
        response = client.get('/workshops/main-only/tutorial/intro')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.context['is_gated'])
        # Mobile progress bar NOT rendered on the gated page.
        self.assertNotContains(
            response, 'data-testid="reader-mobile-progress-bar"',
            status_code=403,
        )
        self.assertNotContains(
            response, 'data-testid="reader-mobile-drawer-toggle"',
            status_code=403,
        )
        # Title and breadcrumb still render so the page is SEO-indexable.
        self.assertContains(
            response, 'data-testid="page-title"', status_code=403,
        )
        self.assertContains(
            response, 'data-testid="page-breadcrumb"', status_code=403,
        )

    def test_anonymous_on_gated_page_does_not_see_progress_bar(self):
        response = self.client.get('/workshops/main-only/tutorial/intro')
        # Anonymous on a paid-tier wall returns 403 with the teaser.
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(
            response, 'data-testid="reader-mobile-progress-bar"',
            status_code=403,
        )

    def test_main_user_on_paid_workshop_sees_progress_bar(self):
        """Main user on a Main-tier-gated workshop CAN access; bar renders."""
        main_user = User.objects.create_user(
            email='main2@test.com', password='x', tier=self.main_tier,
        )
        client = Client()
        client.force_login(main_user)
        response = client.get('/workshops/main-only/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_gated'])
        self.assertContains(
            response, 'data-testid="reader-mobile-progress-bar"',
        )
        self.assertContains(response, 'Page 1 of 2')


class CourseUnitReaderMobileProgressBarTest(TierSetupMixin, TestCase):
    """The course unit reader exposes the same mobile progress bar with
    ``Lesson N of M`` text relative to the flat module-ordered unit list."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = _make_course_with_units(
            slug='intro-to-llms',
            module_unit_counts=(4, 3, 2),  # 9 units total.
            required_level=LEVEL_OPEN,
        )

    def test_sixth_unit_overall_reads_lesson_six_of_nine(self):
        """Module 2 / Unit 2 is unit #6 across the flat list (4+2)."""
        user = User.objects.create_user(
            email='main-c@test.com', password='x', tier=self.main_tier,
        )
        client = Client()
        client.force_login(user)
        response = client.get('/courses/intro-to-llms/module-2/m2-u2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_current'], 6)
        self.assertEqual(response.context['reader_progress_total'], 9)
        self.assertEqual(response.context['reader_progress_kind'], 'lesson')
        self.assertEqual(
            response.context['reader_mobile_label'], 'Course Navigation',
        )
        self.assertContains(response, 'Lesson 6 of 9')
        self.assertContains(
            response, 'data-testid="reader-mobile-progress-bar"',
        )

    def test_first_unit_reads_lesson_one_of_nine(self):
        user = User.objects.create_user(
            email='main-c2@test.com', password='x', tier=self.main_tier,
        )
        client = Client()
        client.force_login(user)
        response = client.get('/courses/intro-to-llms/module-1/m1-u1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_current'], 1)
        self.assertContains(response, 'Lesson 1 of 9')

    def test_last_unit_reads_lesson_nine_of_nine(self):
        user = User.objects.create_user(
            email='main-c3@test.com', password='x', tier=self.main_tier,
        )
        client = Client()
        client.force_login(user)
        response = client.get('/courses/intro-to-llms/module-3/m3-u2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_current'], 9)
        self.assertContains(response, 'Lesson 9 of 9')


class ReaderMobileProgressBarMarkupContractTest(TierSetupMixin, TestCase):
    """Regression guards on the new partial's markup contract.

    Ensures the partial keeps ``id="sidebar-toggle-btn"`` /
    ``aria-controls="sidebar-nav"`` (so the existing JS in
    `_scripts.html` works), is hidden on `lg+`, and replaces the old
    toggle inside the sidebar `<aside>`.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='contract', title='Contract',
            pages_required_level=LEVEL_OPEN,
            page_data=[
                ('intro', 'Intro', 'body 1'),
                ('next', 'Next', 'body 2'),
            ],
        )

    def test_toggle_keeps_shared_id_and_aria_contract(self):
        response = self.client.get('/workshops/contract/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        # Single sidebar-toggle-btn (the new mobile bar's button) — NOT
        # duplicated across the layout, otherwise the JS handler would
        # bind only the first one.
        body = response.content.decode()
        self.assertEqual(body.count('id="sidebar-toggle-btn"'), 1)
        # Aria-controls still points at the shared #sidebar-nav drawer.
        self.assertIn(
            'aria-controls="sidebar-nav"', body,
        )

    def test_progress_bar_wrapper_is_hidden_on_lg_viewports(self):
        response = self.client.get('/workshops/contract/tutorial/intro')
        self.assertContains(response, 'lg:hidden')
        # The wrapper element holds the lg:hidden class; assert it is
        # adjacent to the testid attribute so we know the class lives
        # on the right element rather than somewhere else in the page.
        body = response.content.decode()
        wrapper_idx = body.find('data-testid="reader-mobile-progress-bar"')
        self.assertGreater(wrapper_idx, -1)
        snippet = body[max(0, wrapper_idx - 80):wrapper_idx]
        self.assertIn('lg:hidden', snippet)

    def test_no_leaked_django_comment_markers_in_rendered_body(self):
        """Per ``feedback_django_comment_leak.md`` regression guard:
        ``{# ... #}`` is single-line; multi-line uses
        ``{% comment %}...{% endcomment %}`` so no opening ``{#`` leaks."""
        response = self.client.get('/workshops/contract/tutorial/intro')
        body = response.content.decode()
        # If a multi-line {# #} ever leaks the literal opening "{#"
        # appears in the rendered HTML.
        self.assertNotIn('{# ', body)
        self.assertNotIn('{#\n', body)

    def test_old_toggle_no_longer_lives_inside_sidebar_aside(self):
        """The mobile toggle moved from inside ``<aside>`` to a new
        partial above the breadcrumb. Regression: the toggle must NOT
        be inside the persistent sidebar column anymore (the bug that
        motivated this issue is exactly that the toggle lived below the
        body on mobile)."""
        response = self.client.get('/workshops/contract/tutorial/intro')
        body = response.content.decode()
        aside_open = body.find('id="content-sidebar-aside"')
        # The aside's closing tag is the next "</aside>" after the open.
        aside_close = body.find('</aside>', aside_open)
        self.assertGreater(aside_open, -1)
        self.assertGreater(aside_close, aside_open)
        aside_block = body[aside_open:aside_close]
        self.assertNotIn('id="sidebar-toggle-btn"', aside_block)


class ReaderMobileProgressBarFillProportionTest(TierSetupMixin, TestCase):
    """The fill bar width reflects ``completed / total`` for authenticated
    users via Django's ``widthratio`` template tag."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='fill', title='Fill',
            pages_required_level=LEVEL_OPEN,
            page_data=[
                ('p1', 'P1', 'b'),
                ('p2', 'P2', 'b'),
                ('p3', 'P3', 'b'),
                ('p4', 'P4', 'b'),
                ('p5', 'P5', 'b'),
            ],
        )

    def test_fill_bar_width_matches_completed_fraction(self):
        """2 of 5 completed -> width: 40%."""
        from content.services import completion as completion_service

        user = User.objects.create_user(
            email='fill@test.com', password='x', tier=self.main_tier,
        )
        # Mark first two pages as completed.
        for slug in ('p1', 'p2'):
            page = WorkshopPage.objects.get(workshop=self.workshop, slug=slug)
            completion_service.mark_completed(user, page)

        client = Client()
        client.force_login(user)
        response = client.get('/workshops/fill/tutorial/p3')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reader_progress_completed'], 2)
        body = response.content.decode()
        # widthratio(2, 5, 100) == 40
        self.assertIn('width: 40%', body)

    def test_fill_bar_zero_when_nothing_completed(self):
        user = User.objects.create_user(
            email='zero@test.com', password='x', tier=self.main_tier,
        )
        client = Client()
        client.force_login(user)
        response = client.get('/workshops/fill/tutorial/p1')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # 0/5 = 0%; the fill div renders but with width: 0%.
        self.assertIn('data-testid="reader-mobile-progress-fill"', body)
        self.assertIn('width: 0%', body)
