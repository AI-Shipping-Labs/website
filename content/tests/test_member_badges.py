"""Regression tests for shared member-facing badges."""

from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


def render_template(source, context=None):
    return Template(source).render(Context(context or {})).strip()


class MemberBadgeRendererTest(SimpleTestCase):
    def test_label_badge_supports_icon_id_and_testid(self):
        html = render_template(
            """
            {% load member_badges %}
            {% member_label_badge "Admin" tone="accent_outline" size="sm" icon="shield" element_id="account-admin-role-badge" testid="account-admin-role-badge" %}
            """,
        )

        self.assertIn('data-component="member-badge"', html)
        self.assertIn('id="account-admin-role-badge"', html)
        self.assertIn('data-testid="account-admin-role-badge"', html)
        self.assertIn('data-lucide="shield"', html)
        self.assertIn('Admin', html)

    def test_tier_badge_uses_public_required_tier_copy(self):
        html = render_template(
            """
            {% load member_badges %}
            {% member_tier_badge 10 %}
            """,
        )

        self.assertIn('Basic or above', html)
        self.assertNotIn('Basic+', html)

    def test_status_badge_uses_visible_text(self):
        html = render_template(
            """
            {% load member_badges %}
            {% member_status_badge "Proposals Open" status="proposals_open" icon="plus" %}
            """,
        )

        self.assertIn('Proposals Open', html)
        self.assertIn('data-lucide="plus"', html)


class MemberBadgeTemplateUsageTest(SimpleTestCase):
    def test_first_batch_templates_load_member_badges(self):
        template_paths = [
            'templates/accounts/account.html',
            'templates/content/blog_list.html',
            'templates/content/courses_list.html',
            'templates/content/_project_card.html',
            'templates/content/activities.html',
            'templates/content/_workshops_catalog.html',
            'templates/content/workshop_detail.html',
            'templates/content/sprints_index.html',
            'templates/home.html',
            'templates/plans/sprint_detail.html',
            'templates/events/_event_header.html',
            'templates/events/_upcoming_event_card.html',
            'templates/events/events_calendar.html',
            'templates/events/events_list.html',
            'templates/voting/poll_list.html',
            'templates/voting/poll_detail.html',
        ]

        for template_path in template_paths:
            with self.subTest(template=template_path):
                source = (ROOT / template_path).read_text()
                self.assertIn('{% load member_badges %}', source)
                self.assertRegex(source, r'{% member_(?:badge|tier_badge|status_badge|label_badge) ')

    def test_scoped_guest_templates_do_not_use_legacy_membership_prefix(self):
        template_paths = [
            'templates/content/activities.html',
            'templates/content/sprints_index.html',
            'templates/home.html',
        ]

        for template_path in template_paths:
            with self.subTest(template=template_path):
                source = (ROOT / template_path).read_text()
                self.assertNotIn('Membership:', source)

    def test_studio_badge_partial_remains_separate(self):
        source = (ROOT / 'templates' / 'studio' / 'includes' / 'status_badge.html').read_text()

        self.assertIn('data-component="studio-status-badge"', source)
        self.assertNotIn('member-badge', source)
