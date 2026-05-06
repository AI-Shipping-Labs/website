"""Tests for the foldable course-unit sidebar (issue #229).

This template-level test only confirms the toggle markup, the localStorage
key, and the CSS hooks are in the rendered HTML. The actual collapse +
persistence behaviour is exercised by a Playwright test
(``playwright_tests/test_foldable_sidebar.py``).
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_OPEN
from content.models import (
    Course,
    Module,
    Unit,
    UserContentCompletion,
    Workshop,
    WorkshopPage,
)
from content.models.completion import CONTENT_TYPE_WORKSHOP_PAGE
from tests.fixtures import TierSetupMixin

User = get_user_model()


class FoldableSidebarMarkupTest(TierSetupMixin, TestCase):
    """The course-unit page renders the foldable-sidebar machinery."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title="Foldable Course",
            slug="foldable-course",
            status="published",
            required_level=LEVEL_OPEN,
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title="Mod 1",
            slug="mod-1",
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title="Unit 1",
            slug="unit-1",
            sort_order=1,
            body="Hello",
        )
        cls.user = User.objects.create_user(
            email="foldable-markup@test.com",
            password="pw12345!",
            email_verified=True,
        )
        cls.user.tier = cls.free_tier
        cls.user.save()
        cls.workshop = Workshop.objects.create(
            title="Foldable Workshop",
            slug="foldable-workshop",
            status="published",
            date=date(2026, 4, 29),
            landing_required_level=LEVEL_OPEN,
            pages_required_level=LEVEL_OPEN,
            recording_required_level=LEVEL_OPEN,
        )
        cls.workshop_page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            title="Workshop Page 1",
            slug="page-1",
            sort_order=1,
            body="Workshop body",
        )
        cls.workshop_page_2 = WorkshopPage.objects.create(
            workshop=cls.workshop,
            title="Workshop Page 2",
            slug="page-2",
            sort_order=2,
            body="More workshop body",
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email="foldable-markup@test.com", password="pw12345!")
        self.url = self.unit.get_absolute_url()

    def test_collapse_button_rendered(self):
        """The in-sidebar collapse button is present on accessible units."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="content-sidebar-collapse-btn"')
        self.assertContains(response, 'data-testid="content-sidebar-collapse-btn"')

    def test_floating_open_button_rendered(self):
        """The floating open-sidebar button is present so it can be revealed."""
        response = self.client.get(self.url)
        self.assertContains(response, 'id="content-sidebar-floating-toggle"')
        self.assertContains(response, 'data-testid="content-sidebar-floating-toggle"')

    def test_aside_and_main_have_layout_ids(self):
        """The sidebar and main columns expose stable IDs the JS targets."""
        response = self.client.get(self.url)
        self.assertContains(response, 'id="content-sidebar-aside"')
        self.assertContains(response, 'id="content-sidebar-main"')

    def test_localstorage_key_referenced(self):
        """The collapse preference is persisted under content-sidebar-collapsed."""
        response = self.client.get(self.url)
        self.assertContains(response, "content-sidebar-collapsed")

    def test_pre_paint_script_sets_data_attribute(self):
        """An inline script applies the collapse state before layout paints
        to avoid a flash of expanded sidebar."""
        response = self.client.get(self.url)
        body = response.content.decode()
        self.assertIn("data-content-sidebar", body)
        # The pre-paint script is in the body itself, not deferred at end.
        # That's how we avoid the flash.
        self.assertIn("setAttribute('data-content-sidebar'", body)

    def test_collapse_css_uses_lg_breakpoint(self):
        """Collapse CSS is gated to lg+ so mobile keeps its hamburger."""
        response = self.client.get(self.url)
        body = response.content.decode()
        self.assertIn("min-width: 1024px", body)
        # And mobile must explicitly hide the desktop toggles.
        self.assertIn("max-width: 1023px", body)

    def test_floating_toggle_uses_panel_left_open_icon(self):
        """The floating reveal-button uses a panel-style icon, not a chevron."""
        response = self.client.get(self.url)
        body = response.content.decode()
        # Pull just the floating-toggle <button>...</button> chunk to assert on it.
        start = body.index('id="content-sidebar-floating-toggle"')
        end = body.index("</button>", start)
        chunk = body[start:end]
        self.assertIn("panel-left-open", chunk)

    def test_collapse_button_uses_panel_left_close_icon(self):
        """The in-sidebar collapse button uses the close-panel icon."""
        response = self.client.get(self.url)
        body = response.content.decode()
        start = body.index('id="content-sidebar-collapse-btn"')
        end = body.index("</button>", start)
        chunk = body[start:end]
        self.assertIn("panel-left-close", chunk)

    def test_studio_sidebar_untouched(self):
        """The studio sidebar must not get the new content-sidebar markup."""
        # The studio is staff-only; just check the markup isn't injected
        # globally. Hitting the unit page should NOT set studio-sidebar IDs.
        response = self.client.get(self.url)
        self.assertNotContains(response, 'id="studio-sidebar"')

    def test_gated_unit_does_not_render_toggles(self):
        """A gated unit page (no access) doesn't show the foldable controls
        because there's no sidebar to fold."""
        gated = Course.objects.create(
            title="Gated Foldable",
            slug="gated-foldable",
            status="published",
            required_level=30,
        )
        gmod = Module.objects.create(
            course=gated, title="GMod", slug="gmod", sort_order=1,
        )
        gunit = Unit.objects.create(
            module=gmod, title="Locked Unit", slug="locked-unit", sort_order=1,
        )
        response = self.client.get(gunit.get_absolute_url())
        # The view returns 403 with a gated render. assertNotContains needs
        # status_code to match.
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(
            response,
            'id="content-sidebar-collapse-btn"',
            status_code=403,
        )
        self.assertNotContains(
            response,
            'id="content-sidebar-floating-toggle"',
            status_code=403,
        )

    def test_workshop_page_renders_shared_reader_controls(self):
        """Accessible workshop tutorial pages use the same reader hooks."""
        response = self.client.get(self.workshop_page.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="content-sidebar-collapse-btn"')
        self.assertContains(response, 'id="content-sidebar-floating-toggle"')
        self.assertContains(response, 'id="content-sidebar-aside"')
        self.assertContains(response, 'id="content-sidebar-main"')
        self.assertContains(response, 'id="sidebar-toggle-btn"')
        self.assertContains(response, 'id="sidebar-nav"')
        self.assertContains(response, 'data-testid="workshop-sidebar"')
        self.assertContains(response, "content-sidebar-collapsed")

    def test_workshop_sidebar_marks_completed_pages(self):
        """Authenticated readers see completed workshop pages in the nav."""
        UserContentCompletion.objects.create(
            user=self.user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id=self.workshop_page.pk,
            completed_at=timezone.now(),
        )
        response = self.client.get(self.workshop_page_2.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sidebar-completed-page"')
        self.assertContains(response, 'check-circle-2')

    def test_gated_workshop_page_does_not_render_reader_controls(self):
        """Workshop paywalls hide body, Q&A, completion, and reader controls."""
        gated = Workshop.objects.create(
            title="Gated Workshop",
            slug="gated-workshop",
            status="published",
            date=date(2026, 4, 29),
            landing_required_level=LEVEL_OPEN,
            pages_required_level=30,
            recording_required_level=30,
        )
        page = WorkshopPage.objects.create(
            workshop=gated,
            title="Locked Page",
            slug="locked-page",
            sort_order=1,
            body="Secret workshop body",
            content_id="11111111-1111-1111-1111-111111111111",
        )
        response = self.client.get(page.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="page-paywall"')
        self.assertNotContains(response, 'Secret workshop body')
        self.assertNotContains(response, 'id="content-sidebar-collapse-btn"')
        self.assertNotContains(response, 'id="content-sidebar-floating-toggle"')
        self.assertNotContains(response, 'data-testid="mark-page-complete-btn"')
        self.assertNotContains(response, 'data-testid="qa-section"')
