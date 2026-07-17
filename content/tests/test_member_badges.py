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

    def test_tier_badge_preserves_numeric_required_level_metadata(self):
        labels = {
            0: 'Free',
            5: 'Free with sign-in',
            10: 'Basic or above',
            20: 'Main or above',
            30: 'Premium',
        }

        for level, label in labels.items():
            with self.subTest(level=level):
                html = render_template(
                    """
                    {% load member_badges %}
                    {% member_tier_badge level %}
                    """,
                    {'level': level},
                )
                self.assertIn(f'data-required-level="{level}"', html)
                self.assertIn(label, html)

    def test_non_tier_badges_do_not_emit_required_level_metadata(self):
        html = render_template(
            """
            {% load member_badges %}
            {% member_label_badge "Admin" %}
            {% member_status_badge "Registered" %}
            """,
        )

        self.assertNotIn('data-required-level', html)

    def test_status_badge_uses_visible_text(self):
        html = render_template(
            """
            {% load member_badges %}
            {% member_status_badge "Proposals Open" status="proposals_open" icon="plus" %}
            """,
        )

        self.assertIn('Proposals Open', html)
        self.assertIn('data-lucide="plus"', html)

    def test_past_status_badge_uses_neutral_tone(self):
        html = render_template(
            """
            {% load member_badges %}
            {% member_status_badge "Past" status="past" %}
            """,
        )

        self.assertIn('bg-secondary', html)
        self.assertIn('text-muted-foreground', html)
        self.assertNotIn('bg-green-', html)
        self.assertNotIn('text-green-', html)

    def test_status_tones_use_theme_safe_semantic_palette(self):
        expected_classes = {
            'active': 'bg-green-500/15 text-green-800 dark:text-green-400',
            'closed': 'bg-red-500/15 text-red-800 dark:text-red-400',
            'enrolled': 'bg-green-500/15 text-green-800 dark:text-green-400',
            'upcoming': 'bg-blue-500/15 text-blue-800 dark:text-blue-400',
            'registered': 'bg-green-500/15 text-green-800 dark:text-green-400',
            'open': 'bg-green-500/15 text-green-800 dark:text-green-400',
            'proposals_open': 'bg-green-500/15 text-green-800 dark:text-green-400',
            'cancelled': 'bg-red-500/15 text-red-800 dark:text-red-400',
            'submitted': 'bg-yellow-500/15 text-yellow-800 dark:text-yellow-400',
            'in_review': 'bg-blue-500/15 text-blue-800 dark:text-blue-400',
            'review_complete': 'bg-green-500/15 text-green-800 dark:text-green-400',
            'certified': 'bg-purple-500/15 text-purple-800 dark:text-purple-400',
            'pending': 'bg-yellow-500/15 text-yellow-800 dark:text-yellow-400',
        }

        for status, classes in expected_classes.items():
            with self.subTest(status=status):
                html = render_template(
                    """
                    {% load member_badges %}
                    {% member_status_badge label status=status %}
                    """,
                    {'label': status.title(), 'status': status},
                )
                for class_name in classes.split():
                    self.assertIn(class_name, html)


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
            'templates/events/event_series.html',
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
