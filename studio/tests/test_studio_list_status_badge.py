"""Regression tests for Studio list-page body-table consistency.

Issue #753 — Studio list-page body table consistency (audit #747, classes 5
and 12). Three list templates (``sprints``, ``downloads``, ``imports``)
previously hand-rolled status pill markup instead of using the shared
``{% studio_status_badge %}`` template tag. ``ACTION_GROUP_CLASS`` also used
``flex-wrap`` which caused row-action pills to wrap onto a second line at
1280px viewports — the canonical fix is ``flex-nowrap`` plus the table's
existing ``overflow-x-auto`` wrapper.

These tests pin the unified surface in place so we don't regress back to
hand-rolled pills or to ``flex-wrap``.
"""

from pathlib import Path

from django.test import TestCase

REPO_ROOT = Path(__file__).resolve().parents[2]


class StudioListStatusBadgeTemplateTest(TestCase):
    """Status pills on sprints/downloads/imports go through the shared tag."""

    template_paths = [
        'templates/studio/sprints/list.html',
        'templates/studio/downloads/list.html',
        'templates/studio/imports/list.html',
    ]

    # Hand-rolled pill class strings that previously lived in each row cell.
    # These are scoped checks: the strings may legitimately appear elsewhere
    # (other surfaces, other badges) — we only assert they're gone from each
    # specific list-row template.
    hand_rolled_pill_substrings = {
        'templates/studio/sprints/list.html': (
            'text-xs px-2 py-1 rounded-full bg-secondary text-foreground',
        ),
        'templates/studio/downloads/list.html': (
            'text-xs px-2 py-1 rounded-full {% if download.published %}',
            'text-xs px-2 py-1 rounded-full bg-yellow-500/20',
        ),
        'templates/studio/imports/list.html': (
            "{% if batch.status == 'failed' %}",
            "{% elif batch.status == 'completed' %}",
        ),
    }

    def _template_source(self, relative_path):
        return (REPO_ROOT / relative_path).read_text()

    def test_each_list_template_calls_studio_status_badge(self):
        for path in self.template_paths:
            with self.subTest(path=path):
                source = self._template_source(path)
                self.assertIn(
                    'studio_status_badge',
                    source,
                    msg=(
                        f"{path} must call {{% studio_status_badge %}} for "
                        'the Status column — found no usage.'
                    ),
                )

    def test_hand_rolled_pill_markup_is_gone(self):
        for path, substrings in self.hand_rolled_pill_substrings.items():
            source = self._template_source(path)
            for needle in substrings:
                with self.subTest(path=path, needle=needle):
                    self.assertNotIn(
                        needle,
                        source,
                        msg=(
                            f"{path} still contains hand-rolled pill markup "
                            f"({needle!r}). Use {{% studio_status_badge %}}."
                        ),
                    )


class StudioActionGroupNoWrapTest(TestCase):
    """``ACTION_GROUP_CLASS`` must use ``flex-nowrap`` (#753 class 12)."""

    def test_action_group_class_uses_flex_nowrap(self):
        from studio.templatetags.studio_filters import (
            ACTION_GROUP_CLASS,
            studio_list_class,
        )

        self.assertIn('flex-nowrap', ACTION_GROUP_CLASS)
        self.assertNotIn('flex-wrap', ACTION_GROUP_CLASS)

        rendered = studio_list_class('action_group')
        self.assertIn('flex-nowrap', rendered)
        self.assertNotIn('flex-wrap', rendered)


class StudioStatusBadgePaletteTest(TestCase):
    """The shared palette covers sprint and import statuses (#753 class 5)."""

    def test_status_badge_classes_contain_new_keys(self):
        from studio.templatetags.studio_filters import STATUS_BADGE_CLASSES

        self.assertEqual(
            STATUS_BADGE_CLASSES['active'],
            'bg-green-500/20 text-green-700 dark:text-green-300',
        )
        self.assertEqual(
            STATUS_BADGE_CLASSES['archived'],
            'bg-secondary text-muted-foreground',
        )
        self.assertEqual(
            STATUS_BADGE_CLASSES['failed'],
            'bg-red-500/20 text-red-700 dark:text-red-300',
        )
        self.assertEqual(
            STATUS_BADGE_CLASSES['running'],
            'bg-blue-500/20 text-blue-700 dark:text-blue-300',
        )
        self.assertEqual(
            STATUS_BADGE_CLASSES['sent'],
            'bg-green-500/20 text-green-700 dark:text-green-300',
        )
        self.assertEqual(
            STATUS_BADGE_CLASSES['sending'],
            'bg-blue-500/20 text-blue-700 dark:text-blue-300',
        )

    def test_tier_and_user_status_helpers_use_light_dark_foregrounds(self):
        from studio.templatetags.studio_filters import (
            TIER_PILL_CLASSES,
            USER_STATUS_PILL_CLASSES,
            studio_tier_pill_classes,
            studio_user_status_pill_classes,
        )

        expected_tiers = {
            'basic': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
            'premium': 'bg-amber-500/20 text-amber-700 dark:text-amber-300',
        }
        expected_user_statuses = {
            'active': 'bg-green-500/15 text-green-700 dark:text-green-300',
            'staff': 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
            'inactive': 'bg-red-500/15 text-red-700 dark:text-red-300',
        }

        for slug, classes in expected_tiers.items():
            with self.subTest(tier=slug):
                self.assertEqual(TIER_PILL_CLASSES[slug], classes)
                self.assertEqual(studio_tier_pill_classes(slug), classes)

        for status, classes in expected_user_statuses.items():
            with self.subTest(status=status):
                self.assertEqual(USER_STATUS_PILL_CLASSES[status], classes)
                self.assertEqual(studio_user_status_pill_classes(status), classes)
