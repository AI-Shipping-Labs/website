"""Regression tests for Studio list-page table wrappers being mobile-safe.

Issue #760: ``/studio/plans/`` and ``/studio/sprints/`` rendered the
legacy wrapper ``bg-card border border-border rounded-lg overflow-hidden``
instead of the canonical ``LIST_TABLE_WRAPPER_CLASS``
(``studio-responsive-table bg-card border border-border rounded-lg
overflow-x-auto``).

At 393x851 the Actions column clipped past the viewport with no scroll
affordance and ``studio-responsive-table``'s row-stacking CSS (defined
in ``templates/studio/base.html`` lines 16-144) never fired, because
the wrapper element did not carry the trigger class.

The fix swaps the wrapper to the canonical class. This test makes sure
every Studio ``list.html`` template keeps both signals on the outer
table wrapper:

- ``studio-responsive-table`` so mobile row-stacking activates.
- ``overflow-x-auto`` so even when row-stacking is not in play the
  Actions column stays scrollable instead of being clipped.

These are grep-shaped assertions intentionally: they catch drift in any
future list page without needing per-page integration coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.template import Context, Engine
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_TEMPLATES_DIR = REPO_ROOT / 'templates' / 'studio'

# Match a wrapper ``<div>`` that owns a table. We look for the
# ``rounded-lg`` + (``overflow-x-auto`` | ``overflow-hidden``) class pair
# that the legacy and canonical wrappers both used, OR the templatetag
# call ``{% studio_list_class 'wrapper' %}`` which renders the canonical
# class string at template-render time.
WRAPPER_LINE_PATTERN = re.compile(
    r'<div\s+class="([^"]*?(?:rounded-lg\s+overflow-x-auto|rounded-lg\s+overflow-hidden|'
    r'studio_list_class\s+\'wrapper\')[^"]*)"'
)

# Wrappers we expect to find — at minimum, every list page that renders
# a table-shaped body. Some list templates (e.g. ``imports/list.html``)
# have multiple cards on the page; we only care about the wrapper that
# contains the actual ``<table>``.
LIST_TEMPLATES = sorted(STUDIO_TEMPLATES_DIR.glob('*/list.html'))


def _render_wrapper_class(template_source_class_attr: str) -> str:
    """Render Django template tags inside a wrapper ``class="..."`` value.

    The wrapper might be a literal class string OR
    ``{% studio_list_class 'wrapper' %}``. For grep-style assertions we
    need to expand the tag to its final class string.
    """
    if '{%' not in template_source_class_attr:
        return template_source_class_attr
    engine = Engine.get_default()
    # Wrap the snippet in a minimal template that loads our tag library.
    template = engine.from_string(
        '{% load studio_filters %}' + template_source_class_attr
    )
    return template.render(Context({}))


def _table_wrapper_classes_for(template_path: Path) -> list[str]:
    """Return all wrapper class strings on ``<div>`` elements that wrap a
    ``<table>`` (or look like they do).

    A wrapper wraps a table when the next ``<`` element on the page after
    the wrapper opens is a ``<table>``. This is conservative enough to
    avoid false positives from filter cards or empty-state cards while
    still catching every list page's table wrapper.
    """
    source = template_path.read_text()
    classes = []
    # Find wrappers and check that a <table> appears before the matching
    # close <div>. We don't need a real parser — we just scan forward
    # until we either hit "<table" (wrapper owns a table) or a closing
    # </div> at the same nesting depth.
    for match in WRAPPER_LINE_PATTERN.finditer(source):
        class_attr = match.group(1)
        # Look ahead in the source for the next significant tag.
        tail = source[match.end():]
        # Strip whitespace and comments to find the first child element.
        # We accept up to ~400 chars of skip (typical pager includes,
        # data-attrs, comments) before declaring "no table here".
        snippet = tail[:600]
        # If there's a <table within this lookahead OR an {% include
        # ... pager %} immediately followed by a table, count it.
        if '<table' in snippet or '{% include' in snippet and '<table' in tail[:1500]:
            classes.append(_render_wrapper_class(class_attr))
    return classes


class StudioListWrapperResponsiveTest(SimpleTestCase):
    """Every list-page table wrapper carries the mobile-safe class pair."""

    def test_at_least_some_list_templates_were_found(self):
        # Sanity: if a future refactor renames list templates this test
        # must scream rather than silently pass with zero coverage.
        self.assertGreater(
            len(LIST_TEMPLATES), 10,
            f'expected >10 studio list templates, found {len(LIST_TEMPLATES)}'
        )

    def test_no_list_template_uses_overflow_hidden_on_table_wrapper(self):
        """``overflow-hidden`` clips the Actions column on mobile."""
        offenders = []
        for template in LIST_TEMPLATES:
            for class_attr in _table_wrapper_classes_for(template):
                if 'overflow-hidden' in class_attr:
                    offenders.append(
                        f'{template.relative_to(REPO_ROOT)}: {class_attr}'
                    )
        self.assertEqual(offenders, [], offenders)

    def test_every_list_table_wrapper_has_studio_responsive_table(self):
        """Without ``studio-responsive-table`` the mobile row-stacking
        CSS in ``templates/studio/base.html`` never fires."""
        missing = []
        for template in LIST_TEMPLATES:
            wrappers = _table_wrapper_classes_for(template)
            if not wrappers:
                # No table on this page (e.g. an empty-state-only list);
                # skip rather than fail.
                continue
            for class_attr in wrappers:
                if 'studio-responsive-table' not in class_attr:
                    missing.append(
                        f'{template.relative_to(REPO_ROOT)}: {class_attr}'
                    )
        self.assertEqual(missing, [], missing)

    def test_every_list_table_wrapper_has_overflow_x_auto(self):
        """Even when row-stacking does not match, the Actions column
        must stay reachable via horizontal scroll instead of being
        clipped under ``overflow-hidden``."""
        missing = []
        for template in LIST_TEMPLATES:
            wrappers = _table_wrapper_classes_for(template)
            if not wrappers:
                continue
            for class_attr in wrappers:
                if 'overflow-x-auto' not in class_attr:
                    missing.append(
                        f'{template.relative_to(REPO_ROOT)}: {class_attr}'
                    )
        self.assertEqual(missing, [], missing)

    def test_plans_list_uses_canonical_wrapper(self):
        """Pin the specific regression from #760 — plans table."""
        source = (STUDIO_TEMPLATES_DIR / 'plans' / 'list.html').read_text()
        # The wrapper must reference the canonical class — either via
        # the tag or as a literal string.
        self.assertNotIn(
            'bg-card border border-border rounded-lg overflow-hidden',
            source,
            'plans/list.html still carries the legacy overflow-hidden wrapper',
        )
        wrappers = _table_wrapper_classes_for(STUDIO_TEMPLATES_DIR / 'plans' / 'list.html')
        self.assertTrue(wrappers, 'plans/list.html had no detectable table wrapper')
        for class_attr in wrappers:
            self.assertIn('studio-responsive-table', class_attr)
            self.assertIn('overflow-x-auto', class_attr)

    def test_sprints_list_uses_canonical_wrapper(self):
        """Pin the specific regression from #760 — sprints table."""
        source = (STUDIO_TEMPLATES_DIR / 'sprints' / 'list.html').read_text()
        self.assertNotIn(
            'bg-card border border-border rounded-lg overflow-hidden',
            source,
            'sprints/list.html still carries the legacy overflow-hidden wrapper',
        )
        wrappers = _table_wrapper_classes_for(STUDIO_TEMPLATES_DIR / 'sprints' / 'list.html')
        self.assertTrue(wrappers, 'sprints/list.html had no detectable table wrapper')
        for class_attr in wrappers:
            self.assertIn('studio-responsive-table', class_attr)
            self.assertIn('overflow-x-auto', class_attr)
