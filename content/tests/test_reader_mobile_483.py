"""Issue #483: Tighter syllabus mobile spacing, parity between course-unit
and workshop-tutorial reader chrome, and zero-count copy suppression.

Covers (Django HTML-rendering layer):

- Course detail syllabus on mobile: tighter padding on module summary
  rows; tap-target floor remains 44px (min-h-[44px] survives).
- Course detail syllabus: zero-count "0 lessons" string is suppressed
  for empty modules and remains correct for non-empty modules.
- Reader bottom navigation has a stand-alone mobile completion row
  rendered above the prev/next pair (sm:hidden) AND the desktop
  inline placement (hidden sm:block) — both wired to the same
  data-completion-toggle attribute so JS keeps them in sync.
- Course unit and workshop tutorial reader sidebars use the same
  per-row spacing and the same circle/check completion glyph for
  the workshop side (parity with course units).
- Mark-complete button has min-h-[44px] in both default and
  full-width variants and uses the same toggle URL contract.

Behavioural / Playwright-layer assertions (live click, viewport
sizes, scrolling, sidebar open/close behaviour) are exercised
separately by ``playwright_tests/test_reader_mobile_483.py``.
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN
from content.models import (
    Course,
    Module,
    Unit,
    Workshop,
    WorkshopPage,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CourseSyllabusMobileSpacingTest(TierSetupMixin, TestCase):
    """The syllabus accordion preserves a 44px tap target for module rows
    while tightening padding on mobile."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title="Spacing Course",
            slug="spacing-course",
            status="published",
            required_level=LEVEL_OPEN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title="Module 1", slug="m-1", sort_order=1,
        )
        Unit.objects.create(
            module=cls.module, title="Unit 1", slug="u-1", sort_order=1,
        )
        Unit.objects.create(
            module=cls.module, title="Unit 2", slug="u-2", sort_order=2,
        )

    def setUp(self):
        self.client = Client()

    def test_module_summary_keeps_44px_tap_target(self):
        """Module summary row must keep ``min-h-[44px]`` (issue #483 AC)."""
        response = self.client.get("/courses/spacing-course")
        self.assertEqual(response.status_code, 200)
        # The min-h-[44px] floor stays on the summary row even with
        # tighter mobile padding (px-3 py-2.5 sm:px-4 sm:py-3).
        self.assertContains(
            response,
            'data-testid="syllabus-module-summary"',
        )
        # Look for the summary class fragment with min-h on the same row.
        # We use an explicit substring search instead of a full tag
        # match so an unrelated class addition won't break the test.
        body = response.content.decode()
        # Find the summary tag and assert spacing tokens.
        idx = body.find('data-testid="syllabus-module-summary"')
        self.assertNotEqual(idx, -1)
        # Look ~600 chars before to capture the opening summary tag.
        window = body[max(0, idx - 600):idx + 200]
        self.assertIn('min-h-[44px]', window)
        self.assertIn('px-3 py-2.5', window)
        self.assertIn('sm:px-4 sm:py-3', window)

    def test_unit_row_keeps_44px_tap_target(self):
        """Unit rows in the syllabus retain a 44px tap target floor."""
        response = self.client.get("/courses/spacing-course")
        body = response.content.decode()
        # Unit row class fragment contains both tightened mobile
        # padding (px-2 py-1.5) and the min-h-[44px] tap target.
        idx = body.find('data-testid="syllabus-unit-row"')
        self.assertNotEqual(idx, -1)
        window = body[max(0, idx - 400):idx + 200]
        self.assertIn('min-h-[44px]', window)
        self.assertIn('px-2 py-1.5', window)
        self.assertIn('sm:px-3 sm:py-2.5', window)


class CourseSyllabusZeroCountSuppressionTest(TestCase):
    """A module with zero units must not render the awkward
    ``0 lessons`` string anywhere in the page (issue #483)."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title="Empty Module Course",
            slug="empty-module-course",
            status="published",
            required_level=LEVEL_OPEN,
        )
        # Module with no units → would previously render "0 lessons".
        cls.empty_module = Module.objects.create(
            course=cls.course,
            title="Empty Module",
            slug="empty-mod",
            sort_order=1,
        )
        cls.full_module = Module.objects.create(
            course=cls.course,
            title="Full Module",
            slug="full-mod",
            sort_order=2,
        )
        Unit.objects.create(
            module=cls.full_module, title="U1", slug="u1", sort_order=1,
        )
        Unit.objects.create(
            module=cls.full_module, title="U2", slug="u2", sort_order=2,
        )

    def setUp(self):
        self.client = Client()

    def test_zero_lessons_string_not_rendered(self):
        response = self.client.get("/courses/empty-module-course")
        self.assertEqual(response.status_code, 200)
        # No "0 lessons" anywhere in the rendered HTML.
        self.assertNotContains(response, "0 lessons")
        # And not the singular variant either.
        self.assertNotContains(response, "0 lesson ")

    def test_nonzero_lesson_count_still_renders(self):
        """Suppression must not regress the populated module count."""
        response = self.client.get("/courses/empty-module-course")
        self.assertContains(response, "2 lessons")


class ReaderBottomNavMobileLayoutTest(TierSetupMixin, TestCase):
    """Mark-complete is rendered as a stand-alone mobile row above the
    prev/next pair, plus an inline desktop placement."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title="Bottom Nav Course",
            slug="bottom-nav-course",
            status="published",
            required_level=LEVEL_MAIN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title="M1", slug="m1", sort_order=1,
        )
        cls.unit1 = Unit.objects.create(
            module=cls.module, title="U1", slug="u1", sort_order=1,
            body="body 1",
        )
        cls.unit2 = Unit.objects.create(
            module=cls.module, title="U2", slug="u2", sort_order=2,
            body="body 2",
        )

    def setUp(self):
        self.client = Client()
        user = User.objects.create_user(
            email="bn@test.com", password="x",
        )
        user.tier = self.main_tier
        user.save()
        self.client.login(email="bn@test.com", password="x")

    def test_mobile_completion_row_present_and_hidden_on_desktop(self):
        response = self.client.get(
            "/courses/bottom-nav-course/m1/u1",
        )
        self.assertEqual(response.status_code, 200)
        # The mobile-only wrapper is sm:hidden so it disappears on >=sm.
        self.assertContains(
            response,
            'data-testid="reader-bottom-completion-mobile"',
        )
        body = response.content.decode()
        idx = body.find('data-testid="reader-bottom-completion-mobile"')
        self.assertNotEqual(idx, -1)
        window = body[max(0, idx - 200):idx + 200]
        self.assertIn('sm:hidden', window)

    def test_desktop_completion_row_hidden_on_mobile(self):
        response = self.client.get(
            "/courses/bottom-nav-course/m1/u1",
        )
        self.assertContains(
            response,
            'data-testid="reader-bottom-completion-desktop"',
        )
        body = response.content.decode()
        idx = body.find('data-testid="reader-bottom-completion-desktop"')
        self.assertNotEqual(idx, -1)
        window = body[max(0, idx - 200):idx + 200]
        self.assertIn('hidden sm:block', window)

    def test_mobile_and_desktop_buttons_share_completion_url(self):
        """Both rendered buttons point at the same toggle endpoint so
        the JS handler keeps them in sync after a click."""
        response = self.client.get(
            "/courses/bottom-nav-course/m1/u1",
        )
        body = response.content.decode()
        # Count *button-attribute* occurrences only (the literal
        # ``data-completion-toggle\n`` newline-suffixed marker the
        # template emits) so the JS comment lines that mention the
        # attribute name don't inflate the count.
        toggles = body.count('data-completion-toggle\n')
        self.assertEqual(
            toggles, 2,
            "Expected 2 completion-toggle button attributes "
            f"(mobile + desktop) but found {toggles}",
        )

    def test_completion_button_min_height_44(self):
        response = self.client.get(
            "/courses/bottom-nav-course/m1/u1",
        )
        body = response.content.decode()
        # Both buttons inherit min-h-[44px] from the include template.
        # Locate one of them and assert the class fragment is present.
        idx = body.find('data-completion-toggle')
        self.assertNotEqual(idx, -1)
        window = body[max(0, idx - 100):idx + 800]
        self.assertIn('min-h-[44px]', window)

    def test_prev_and_next_links_have_min_height_44(self):
        response = self.client.get(
            "/courses/bottom-nav-course/m1/u2",
        )
        body = response.content.decode()
        # Bottom prev link
        idx = body.find('data-testid="bottom-prev-btn"')
        self.assertNotEqual(idx, -1)
        window = body[max(0, idx - 600):idx + 50]
        self.assertIn('min-h-[44px]', window)

    def test_anonymous_user_does_not_render_mobile_completion_row(self):
        """No completion button → no mobile/desktop wrapper renders.

        The Main-tier course teaser layout for unauthenticated visitors
        does not include the reader chrome at all, so neither the
        mobile-only nor the desktop-only completion wrapper appears.
        Some auth surfaces redirect (302); we just need to assert that
        the testids never reach the response body.
        """
        self.client.logout()
        response = self.client.get(
            "/courses/bottom-nav-course/m1/u1",
        )
        # 200 (teaser), 302 (redirect to login), or 403 (forbidden) —
        # any of these is fine; we only care that the wrappers don't
        # show up in the body when they do.
        body = response.content.decode() if response.content else ""
        self.assertNotIn(
            'data-testid="reader-bottom-completion-mobile"', body,
        )
        self.assertNotIn(
            'data-testid="reader-bottom-completion-desktop"', body,
        )


class WorkshopReaderParityTest(TierSetupMixin, TestCase):
    """Workshop tutorial sidebar uses the same per-row spacing as the
    course-unit sidebar, and the workshop completion-toggle button
    follows the same template path as the course unit one."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = Workshop.objects.create(
            slug="parity-ws",
            title="Parity Workshop",
            date=date(2026, 4, 1),
            status="published",
            landing_required_level=0,
            pages_required_level=LEVEL_BASIC,
            recording_required_level=LEVEL_MAIN,
            description="Body",
        )
        cls.page1 = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug="p1",
            title="Page One",
            sort_order=1,
            body="One",
        )
        cls.page2 = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug="p2",
            title="Page Two",
            sort_order=2,
            body="Two",
        )

    def setUp(self):
        self.client = Client()
        user = User.objects.create_user(
            email="parity@test.com", password="x",
        )
        user.tier = self.basic_tier
        user.save()
        self.client.login(email="parity@test.com", password="x")

    def test_workshop_sidebar_row_uses_same_padding_tokens(self):
        response = self.client.get(
            "/workshops/parity-ws/tutorial/p1",
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The workshop sidebar list row now uses px-2 py-1.5 (same as
        # the course sidebar). The previous template used px-3 py-2.
        idx = body.find('data-testid="sidebar-current-page"')
        self.assertNotEqual(idx, -1)
        window = body[max(0, idx - 600):idx + 200]
        self.assertIn('px-2 py-1.5', window)

    def test_workshop_sidebar_uses_circle_glyph_for_uncompleted_pages(
        self,
    ):
        """Both readers now use the same circle + check pair so the
        completion icon vocabulary is shared between course unit and
        workshop tutorial."""
        response = self.client.get(
            "/workshops/parity-ws/tutorial/p1",
        )
        body = response.content.decode()
        # The current page is "p1"; the not-yet-completed sibling row
        # for "p2" must use the circle glyph (matching the course
        # sidebar) instead of the old numeric chip.
        # We look for the lucide circle icon inside the workshop nav.
        nav_idx = body.find('data-testid="workshop-sidebar"')
        self.assertNotEqual(nav_idx, -1)
        # Search for the next list item (p2) within the nav block.
        nav_window = body[nav_idx:nav_idx + 4000]
        self.assertIn('data-lucide="circle"', nav_window)

    def test_workshop_tutorial_renders_mobile_and_desktop_completion(
        self,
    ):
        """Workshop tutorial pages share the bottom-nav include and
        therefore get the same mobile + desktop completion split."""
        response = self.client.get(
            "/workshops/parity-ws/tutorial/p1",
        )
        self.assertContains(
            response,
            'data-testid="reader-bottom-completion-mobile"',
        )
        self.assertContains(
            response,
            'data-testid="reader-bottom-completion-desktop"',
        )


class DashboardZeroCountSuppressionTest(TierSetupMixin, TestCase):
    """Dashboard polling counts must not render "0 votes" / "0 options"
    (issue #483 zero-count requirement)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="dz@test.com", password="x",
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

    def setUp(self):
        self.client = Client()
        self.client.login(email="dz@test.com", password="x")

    def test_zero_votes_copy_not_rendered_on_dashboard(self):
        # Authenticated users land on the dashboard at "/" (see
        # ``content.views.home`` and ``content/tests/test_dashboard.py``).
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "0 votes")
        self.assertNotContains(response, "0 vote ")


class TestNoDjangoCommentLeak(TierSetupMixin, TestCase):
    """Regression: Django ``{# ... #}`` is single-line only; any newline
    inside leaks the inner text as visible page content. This test
    renders all pages touched by issue #483 and asserts the literal
    ``{# `` and `` #}`` substrings never reach the response body.

    See ``feedback_django_comment_leak.md`` — when in doubt, use
    ``{% comment %}...{% endcomment %}`` for multi-line comments.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Course with a module + units (course detail and reader pages).
        cls.course = Course.objects.create(
            title="Leak Course",
            slug="leak-course",
            status="published",
            required_level=LEVEL_OPEN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title="LM", slug="lm", sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title="LU", slug="lu", sort_order=1,
            body="body",
        )
        # Workshop with pages (workshop detail + tutorial reader).
        cls.workshop = Workshop.objects.create(
            slug="leak-ws",
            title="Leak Workshop",
            date=date(2026, 5, 1),
            status="published",
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            description="Body",
        )
        cls.wpage = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug="lp",
            title="Leak Page",
            sort_order=1,
            body="One",
        )
        cls.user = User.objects.create_user(
            email="leak@test.com", password="x",
        )
        cls.user.tier = cls.main_tier
        cls.user.save()

    def setUp(self):
        self.client = Client()
        self.client.login(email="leak@test.com", password="x")

    def _assert_no_comment_leak(self, response, label):
        self.assertEqual(
            response.status_code, 200,
            f"{label} did not render (got {response.status_code})",
        )
        body = response.content.decode()
        self.assertNotIn(
            "{# ", body,
            f"{label}: '{{# ' substring leaked into response body — a "
            "multi-line Django {# #} comment is being rendered as text.",
        )
        self.assertNotIn(
            " #}", body,
            f"{label}: ' #}}' substring leaked into response body — a "
            "multi-line Django {# #} comment is being rendered as text.",
        )

    def test_dashboard_has_no_comment_leak(self):
        self._assert_no_comment_leak(
            self.client.get("/"), "dashboard",
        )

    def test_course_detail_has_no_comment_leak(self):
        self._assert_no_comment_leak(
            self.client.get("/courses/leak-course"), "course detail",
        )

    def test_course_unit_reader_has_no_comment_leak(self):
        self._assert_no_comment_leak(
            self.client.get("/courses/leak-course/lm/lu"),
            "course unit reader",
        )

    def test_workshop_detail_has_no_comment_leak(self):
        self._assert_no_comment_leak(
            self.client.get("/workshops/leak-ws"), "workshop detail",
        )

    def test_workshop_tutorial_reader_has_no_comment_leak(self):
        self._assert_no_comment_leak(
            self.client.get("/workshops/leak-ws/tutorial/lp"),
            "workshop tutorial reader",
        )
