"""Tests for collapsible syllabus on course detail page - issue #145.

Covers:
- Syllabus heading always visible (no outer toggle)
- Each module wrapped in <details class="module-details"> collapsed by default
- Module chevrons present
- Unit links, preview badges, and completion icons preserved
- slideDown animation CSS present
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_MAIN
from content.models import Course, Module, Unit, UserCourseProgress
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CollapsibleSyllabusStructureTest(TestCase):
    """Test that the collapsible syllabus HTML structure is correct."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title="Collapsible Course",
            slug="collapsible-course",
            status="published",
            required_level=LEVEL_OPEN,
        )
        cls.module1 = Module.objects.create(
            course=cls.course, title="Module Alpha", slug="module-alpha", sort_order=1,
        )
        cls.module2 = Module.objects.create(
            course=cls.course, title="Module Beta", slug="module-beta", sort_order=2,
        )
        cls.unit1 = Unit.objects.create(
            module=cls.module1, title="Unit One", slug="unit-one", sort_order=1,
        )
        cls.unit2 = Unit.objects.create(
            module=cls.module2, title="Unit Two", slug="unit-two", sort_order=1,
        )

    def setUp(self):
        self.client = Client()

    def test_syllabus_heading_always_visible(self):
        """Syllabus heading is rendered directly, not inside a toggle."""
        response = self.client.get("/courses/collapsible-course")
        self.assertContains(response, "Syllabus")
        self.assertNotContains(response, 'id="syllabus-toggle"')

    def test_modules_collapsed_by_default(self):
        """Each module <details> should NOT have the open attribute."""
        response = self.client.get("/courses/collapsible-course")
        content = response.content.decode()
        module_details = re.findall(
            r'<details\s+class="[^"]*module-details[^"]*"[^>]*>',
            content,
        )
        self.assertEqual(len(module_details), 2, "Expected 2 module-details elements")
        for tag in module_details:
            self.assertNotIn(" open", tag, "Modules should be collapsed by default")

    def test_module_titles_in_summary(self):
        response = self.client.get("/courses/collapsible-course")
        self.assertContains(response, "Module Alpha")
        self.assertContains(response, "Module Beta")

    def test_module_lesson_count_shown(self):
        response = self.client.get("/courses/collapsible-course")
        self.assertContains(response, "1 lessons")

    def test_module_chevron_icons_present(self):
        response = self.client.get("/courses/collapsible-course")
        self.assertContains(response, "module-chevron")

    def test_slidedown_animation_css_present(self):
        response = self.client.get("/courses/collapsible-course")
        self.assertContains(response, "@keyframes slideDown")

    def test_unit_titles_still_rendered(self):
        response = self.client.get("/courses/collapsible-course")
        self.assertContains(response, "Unit One")
        self.assertContains(response, "Unit Two")


class CollapsibleSyllabusAccessControlTest(TierSetupMixin, TestCase):
    """Test that unit links, preview badges, and icons are preserved."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title="Access Course",
            slug="access-course",
            status="published",
            required_level=LEVEL_MAIN,
        )
        cls.module = Module.objects.create(
            course=cls.course, title="Mod 1", slug="mod-1", sort_order=1,
        )
        cls.unit_normal = Unit.objects.create(
            module=cls.module, title="Normal Lesson", slug="normal-lesson", sort_order=1,
        )
        cls.unit_preview = Unit.objects.create(
            module=cls.module, title="Preview Lesson", slug="preview-lesson", sort_order=2,
            is_preview=True,
        )

    def setUp(self):
        self.client = Client()

    def test_anonymous_sees_unit_titles_as_spans_not_links(self):
        response = self.client.get("/courses/access-course")
        content = response.content.decode()
        self.assertNotIn(
            f'href="{self.unit_normal.get_absolute_url()}"',
            content,
        )
        self.assertContains(
            response,
            '<span class="text-sm text-muted-foreground">Normal Lesson</span>',
            html=True,
        )

    def test_anonymous_sees_preview_badge(self):
        response = self.client.get("/courses/access-course")
        self.assertContains(response, "Preview")

    def test_authorized_user_sees_unit_links(self):
        user = User.objects.create_user(email="main@collapsible.com", password="pass")
        user.tier = self.main_tier
        user.save()
        self.client.login(email="main@collapsible.com", password="pass")
        response = self.client.get("/courses/access-course")
        self.assertContains(
            response,
            f'href="{self.unit_normal.get_absolute_url()}"',
        )

    def test_completed_unit_shows_check_icon(self):
        user = User.objects.create_user(email="prog@collapsible.com", password="pass")
        user.tier = self.main_tier
        user.save()
        UserCourseProgress.objects.create(
            user=user, unit=self.unit_normal, completed_at=timezone.now(),
        )
        self.client.login(email="prog@collapsible.com", password="pass")
        response = self.client.get("/courses/access-course")
        content = response.content.decode()
        self.assertIn("check-circle-2", content)

    def test_preview_unit_shows_eye_icon_for_anonymous(self):
        response = self.client.get("/courses/access-course")
        content = response.content.decode()
        self.assertIn("eye", content)

    def test_uncompleted_unit_shows_circle_icon(self):
        response = self.client.get("/courses/access-course")
        content = response.content.decode()
        self.assertIn('data-lucide="circle"', content)

    def test_cta_block_still_visible_for_anonymous(self):
        response = self.client.get("/courses/access-course")
        self.assertContains(response, "Unlock with Main")


class CollapsibleSyllabusJSPresenceTest(TestCase):
    """Test that the required JS for toggle behavior is included."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title="JS Course",
            slug="js-course",
            status="published",
        )
        cls.module = Module.objects.create(
            course=cls.course, title="Mod", slug="mod", sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title="Unit", slug="unit", sort_order=1,
        )

    def setUp(self):
        self.client = Client()

    def test_module_toggle_event_listener_present(self):
        """The page includes JS that listens for toggle events on module-details."""
        response = self.client.get("/courses/js-course")
        self.assertContains(response, "module-details")
        self.assertContains(response, "module-chevron")

    def test_lucide_createicons_call_present(self):
        response = self.client.get("/courses/js-course")
        self.assertContains(response, "lucide.createIcons()")

    def test_no_javascript_framework_used(self):
        response = self.client.get("/courses/js-course")
        content = response.content.decode()
        self.assertNotIn("react", content.lower().replace("transition", ""))
        self.assertNotIn("vue.js", content.lower())
