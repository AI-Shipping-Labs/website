"""Regression tests for Studio list-page primary CTA consistency.

Issue #742: list-page primary CTAs must use the canonical button class
string defined in ``_docs/design-system.md`` (lines 187-191):

- ``rounded-lg`` (not ``rounded-md``)
- ``transition-opacity`` for the hover-fade interaction
- ``inline-flex items-center justify-center`` so leading icons line up
  with the text label

The grep-shaped assertions below run against every Studio ``list.html``
template under ``templates/studio/`` so that drift in any future list
page is caught at test time rather than visually.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_TEMPLATES_DIR = REPO_ROOT / 'templates' / 'studio'

# A CTA button is ``bg-accent text-accent-foreground`` (the canonical
# accent fill) plus button-shape padding ``px-4 py-2``. This deliberately
# excludes filter pills (``px-3 py-1``), badge dots (no padding), and
# rounded-full tabs which legitimately share the accent fill colour.
CTA_PATTERN = re.compile(
    r'(?=[^"]*\bbg-accent\b)(?=[^"]*\btext-accent-foreground\b)(?=[^"]*\bpx-4 py-2\b)[^"]*'
)


def _iter_list_templates() -> list[Path]:
    return sorted(STUDIO_TEMPLATES_DIR.glob('*/list.html'))


def _cta_class_strings(source: str) -> list[str]:
    """Extract every ``class="..."`` value that matches the CTA pattern."""
    matches = []
    for class_value in re.findall(r'class="([^"]+)"', source):
        if CTA_PATTERN.search(class_value):
            matches.append(class_value)
    return matches


class StudioListCtaConsistencyTest(SimpleTestCase):
    """Every Studio list-page primary CTA must follow the canonical shape."""

    def test_list_templates_discovered(self):
        # Sanity-check: make sure the glob actually finds list pages so a
        # future refactor that renames the templates can't make this
        # whole file silently pass with zero work.
        templates = _iter_list_templates()
        self.assertGreater(len(templates), 5, templates)

    def test_no_cta_uses_rounded_md(self):
        """``rounded-md`` is the form-input radius, not a CTA radius."""
        offenders = []
        for template in _iter_list_templates():
            source = template.read_text()
            for class_value in _cta_class_strings(source):
                if 'rounded-md' in class_value:
                    offenders.append(f'{template.relative_to(REPO_ROOT)}: {class_value}')
        self.assertEqual(offenders, [], offenders)

    def test_no_cta_uses_inline_block(self):
        """``inline-block`` breaks icon+label alignment; CTAs use flex."""
        offenders = []
        for template in _iter_list_templates():
            source = template.read_text()
            for class_value in _cta_class_strings(source):
                if 'inline-block' in class_value:
                    offenders.append(f'{template.relative_to(REPO_ROOT)}: {class_value}')
        self.assertEqual(offenders, [], offenders)

    def test_every_cta_declares_a_transition(self):
        """Accent CTAs must animate the hover-fade or hover-colour swap."""
        offenders = []
        for template in _iter_list_templates():
            source = template.read_text()
            for class_value in _cta_class_strings(source):
                if (
                    'transition-opacity' not in class_value
                    and 'transition-colors' not in class_value
                ):
                    offenders.append(f'{template.relative_to(REPO_ROOT)}: {class_value}')
        self.assertEqual(offenders, [], offenders)

    def test_events_list_new_buttons_use_canonical_shape(self):
        """``events/list.html`` has two icon+label CTAs at lines 14/20.

        Both must use ``inline-flex items-center justify-center`` so
        the leading ``plus`` icon lines up with the label, plus the
        canonical ``rounded-lg`` + ``transition-opacity`` pair.
        """
        source = (REPO_ROOT / 'templates' / 'studio' / 'events' / 'list.html').read_text()
        for testid in ('event-new-button', 'event-series-new-button'):
            with self.subTest(testid=testid):
                # Extract the class attribute on the element with this testid.
                element_match = re.search(
                    rf'<a[^>]*\bdata-testid="{testid}"[^>]*>'
                    rf'|<a[^>]*\bclass="([^"]+)"[^>]*\bdata-testid="{testid}"',
                    source,
                )
                self.assertIsNotNone(element_match, f'missing {testid}')
                # Pull the class string for this anchor.
                anchor_match = re.search(
                    rf'<a\b[^>]*\bdata-testid="{testid}"[^>]*>', source, re.DOTALL,
                )
                self.assertIsNotNone(anchor_match)
                anchor = anchor_match.group(0)
                class_match = re.search(r'class="([^"]+)"', anchor)
                self.assertIsNotNone(class_match, anchor)
                class_value = class_match.group(1)
                self.assertIn('inline-flex', class_value)
                self.assertIn('items-center', class_value)
                self.assertIn('justify-center', class_value)
                self.assertIn('rounded-lg', class_value)
                self.assertIn('transition-opacity', class_value)
                self.assertNotIn('rounded-md', class_value)

    def test_event_series_list_new_button_uses_canonical_shape(self):
        """``event_series/list.html`` line 13 — icon+label CTA."""
        source = (REPO_ROOT / 'templates' / 'studio' / 'event_series' / 'list.html').read_text()
        anchor_match = re.search(
            r'<a\b[^>]*\bdata-testid="event-series-new-button"[^>]*>',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match)
        class_value = re.search(r'class="([^"]+)"', anchor_match.group(0)).group(1)
        self.assertIn('inline-flex', class_value)
        self.assertIn('items-center', class_value)
        self.assertIn('justify-center', class_value)
        self.assertIn('rounded-lg', class_value)
        self.assertIn('transition-opacity', class_value)
        self.assertNotIn('rounded-md', class_value)

    def test_crm_search_button_is_canonical_secondary(self):
        """``crm/list.html`` Search button is a form-submit secondary,
        not a primary accent fill (it sits next to the search input
        and is functionally a filter, not a top-of-page CTA).
        """
        source = (REPO_ROOT / 'templates' / 'studio' / 'crm' / 'list.html').read_text()
        button_match = re.search(
            r'<button\b[^>]*\bdata-testid="crm-search-submit"[^>]*>',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(button_match)
        class_value = re.search(r'class="([^"]+)"', button_match.group(0)).group(1)
        # Secondary CTA from design-system.md:190.
        self.assertIn('bg-secondary', class_value)
        self.assertIn('border', class_value)
        self.assertIn('border-border', class_value)
        self.assertIn('text-foreground', class_value)
        self.assertIn('hover:bg-muted', class_value)
        self.assertIn('transition-colors', class_value)
        # Must NOT be primary.
        self.assertNotIn('bg-accent', class_value)

    def test_canonical_empty_state_cta_uses_inline_flex(self):
        """``templates/studio/includes/empty_state.html`` (the canonical
        Studio empty-state partial introduced in #756) renders the
        fresh-zero CTA with the same inline-flex shape every list page
        now inherits. Campaigns, plans, sprints, redirects, events, etc.
        all share that partial.
        """
        source = (
            REPO_ROOT
            / 'templates' / 'studio' / 'includes' / 'empty_state.html'
        ).read_text()
        # Find the fresh-zero CTA anchor inside the partial.
        anchor_match = re.search(
            r'<a\b[^>]*class="[^"]*bg-accent[^"]*"[^>]*>',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match)
        class_value = re.search(
            r'class="([^"]+)"', anchor_match.group(0),
        ).group(1)
        self.assertIn('inline-flex', class_value)
        self.assertIn('items-center', class_value)
        self.assertIn('justify-center', class_value)
        self.assertNotIn('inline-block', class_value)
        self.assertIn('rounded-lg', class_value)
        self.assertNotIn('rounded-md', class_value)
        self.assertIn('transition-opacity', class_value)
