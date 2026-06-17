"""Regression tests for shared member-facing empty states."""

from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class MemberEmptyStateTemplateUsageTest(SimpleTestCase):
    def test_first_batch_templates_use_member_empty_state_tag(self):
        template_paths = [
            'templates/content/dashboard.html',
            'templates/notifications/notification_list.html',
            'templates/content/tutorials_list.html',
            'templates/content/blog_list.html',
            'templates/content/courses_list.html',
            'templates/content/workshops_list.html',
            'templates/events/events_list.html',
        ]

        for template_path in template_paths:
            with self.subTest(template=template_path):
                source = (ROOT / template_path).read_text()
                self.assertIn('{% load member_empty_state %}', source)
                self.assertIn('{% member_empty_state ', source)

    def test_studio_empty_state_partial_remains_separate(self):
        source = (
            ROOT / 'templates' / 'studio' / 'includes' / 'empty_state.html'
        ).read_text()

        self.assertIn('data-testid="studio-empty-state-fresh"', source)
        self.assertIn('data-testid="studio-empty-state-filter"', source)
        self.assertNotIn('member-empty-state', source)
