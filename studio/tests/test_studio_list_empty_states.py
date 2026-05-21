"""Regression tests for the canonical Studio list-page empty state.

Issue #756 (class 9 of #747): Studio list pages must render their
``filter-zero`` and ``fresh-zero`` empty states through the shared
``{% studio_empty_state %}`` inclusion tag — not hand-rolled
``<tr><td colspan>`` cells or hand-rolled empty cards. The test enforces
that contract at template-source time so the next list page added to
``templates/studio/*/list.html`` can't quietly drift back to bespoke
markup.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_TEMPLATES_DIR = REPO_ROOT / 'templates' / 'studio'
EMPTY_STATE_PARTIAL = (
    STUDIO_TEMPLATES_DIR / 'includes' / 'empty_state.html'
)

# Templates whose body is not a single ``<tbody>`` list and that the
# audit explicitly exempted from the per-tbody assertion. Tests below
# still cover the page-level fresh / filter-zero copy via the partial.
TBODY_ALLOWLIST = {
    # CRM uses filter chips + table; the fresh-zero state is a separate
    # card outside the table. The filter-zero state IS rendered inside
    # tbody via the partial.
    'crm/list.html',
}

# Templates the #756 audit explicitly excluded — pages that cannot
# reasonably reach a zero-row state in production. They get neither a
# filter-zero nor a fresh-zero treatment.
NO_EMPTY_STATE_ALLOWLIST = {
    # email_templates iterates a fixed list of template names baked into
    # the codebase, so the table is never empty.
    'email_templates/list.html',
}

# Templates that intentionally render only one branch of the canonical
# partial (no ``{% studio_empty_state %}`` is wrong for them — they DO
# call the partial, just only the ``filter`` or ``fresh`` form).
PARTIAL_USERS = {
    'events/list.html',
    'articles/list.html',
    'courses/list.html',
    'recordings/list.html',
    'projects/list.html',
    'downloads/list.html',
    'notifications/list.html',
    'users/list.html',
    'api_tokens/list.html',
    'workshops/list.html',
    'plans/list.html',
    'sprints/list.html',
    'redirects/list.html',
    'campaigns/list.html',
    'utm_campaigns/list.html',
    'event_series/list.html',
    'crm/list.html',
    'imports/list.html',
    'ses_events/list.html',
}


def _list_templates() -> list[Path]:
    return sorted(STUDIO_TEMPLATES_DIR.glob('*/list.html'))


def _relative(path: Path) -> str:
    return str(path.relative_to(STUDIO_TEMPLATES_DIR))


def _tbody_blocks(source: str) -> list[str]:
    """Return the inner text of every ``<tbody>...</tbody>`` block."""
    return re.findall(r'<tbody[^>]*>(.*?)</tbody>', source, re.DOTALL)


class StudioEmptyStatePartialFileTest(SimpleTestCase):
    """The shared partial file must exist where the spec says it lives."""

    def test_partial_template_file_exists(self):
        self.assertTrue(
            EMPTY_STATE_PARTIAL.is_file(),
            f'{EMPTY_STATE_PARTIAL} is missing',
        )

    def test_partial_declares_both_kinds(self):
        source = EMPTY_STATE_PARTIAL.read_text()
        self.assertIn("kind == 'fresh'", source)
        self.assertIn("kind == 'filter'", source)
        self.assertIn('data-testid="studio-empty-state-fresh"', source)
        self.assertIn('data-testid="studio-empty-state-filter"', source)


class StudioEmptyStateRenderTest(SimpleTestCase):
    """Render the partial directly to validate its public surface."""

    def _render(self, **kwargs):
        template = Template(
            '{% load studio_filters %}'
            '{% studio_empty_state kind=kind '
            "entity_label=entity_label "
            "entity_label_plural=entity_label_plural "
            'create_url=create_url '
            'clear_url=clear_url '
            'colspan=colspan '
            'cta_label=cta_label '
            'testid_suffix=testid_suffix %}'
        )
        ctx = {
            'kind': kwargs.get('kind'),
            'entity_label': kwargs.get('entity_label', ''),
            'entity_label_plural': kwargs.get('entity_label_plural', ''),
            'create_url': kwargs.get('create_url'),
            'clear_url': kwargs.get('clear_url'),
            'colspan': kwargs.get('colspan', 8),
            'cta_label': kwargs.get('cta_label'),
            'testid_suffix': kwargs.get('testid_suffix', ''),
        }
        return template.render(Context(ctx))

    def test_filter_kind_renders_inline_tr_with_clear_filters_link(self):
        html = self._render(
            kind='filter',
            entity_label='sprint',
            entity_label_plural='sprints',
            clear_url='/studio/sprints/',
            colspan=4,
        )
        self.assertIn('data-testid="studio-empty-state-filter"', html)
        # Inline <tr> — table-aware, keeps the header visible.
        self.assertIn('<tr', html)
        self.assertIn('colspan="4"', html)
        self.assertIn('No sprints match your filters.', html)
        self.assertIn('Clear filters', html)
        self.assertIn('href="/studio/sprints/"', html)
        # Filter mode never emits the fresh card.
        self.assertNotIn('studio-empty-state-fresh', html)

    def test_fresh_kind_renders_card_with_new_entity_cta(self):
        html = self._render(
            kind='fresh',
            entity_label='sprint',
            entity_label_plural='sprints',
            create_url='/studio/sprints/new',
        )
        self.assertIn('data-testid="studio-empty-state-fresh"', html)
        self.assertIn('bg-card', html)
        self.assertIn('No sprints yet.', html)
        # Default CTA label is ``New <entity_label>``.
        self.assertIn('New sprint', html)
        self.assertIn('href="/studio/sprints/new"', html)
        # Fresh mode never emits the filter <tr>.
        self.assertNotIn('studio-empty-state-filter', html)

    def test_fresh_kind_without_create_url_renders_no_cta(self):
        """Workshops, courses, articles, etc. are sync-managed (#756) —
        the partial must render the empty card without a CTA when
        ``create_url`` is omitted."""
        html = self._render(
            kind='fresh',
            entity_label='workshop',
            entity_label_plural='workshops',
        )
        self.assertIn('data-testid="studio-empty-state-fresh"', html)
        self.assertIn('No workshops yet.', html)
        # No CTA anchor at all.
        self.assertNotIn('href=', html)
        self.assertNotIn('New workshop', html)

    def test_fresh_kind_supports_custom_cta_label(self):
        """Pages whose header CTA does not literally say ``New <noun>``
        (api_tokens uses "Create token", utm_campaigns uses "Add Campaign")
        can pass an explicit ``cta_label`` override."""
        html = self._render(
            kind='fresh',
            entity_label='token',
            entity_label_plural='API tokens',
            create_url='/studio/api-tokens/new',
            cta_label='Create token',
        )
        self.assertIn('Create token', html)
        # The default ``New <entity_label>`` text must NOT appear when
        # an explicit label is supplied.
        self.assertNotIn('New token', html)

    def test_testid_suffix_emits_secondary_data_attribute(self):
        """Pages with legacy selectors keep using a secondary attribute
        so existing tests can target the same element by its page-specific
        marker without hijacking the canonical ``data-testid``."""
        html = self._render(
            kind='fresh',
            entity_label='campaign',
            entity_label_plural='campaigns',
            create_url='/studio/campaigns/new',
            testid_suffix='campaigns-empty-state',
        )
        self.assertIn('data-testid="studio-empty-state-fresh"', html)
        self.assertIn('data-empty-state="campaigns-empty-state"', html)


class StudioListEmptyStatesUsePartialTest(SimpleTestCase):
    """Every studio list page must route through the canonical partial."""

    def test_all_18_audited_list_pages_call_the_partial(self):
        """The 18 list pages enumerated in the #756 audit must each
        invoke ``{% studio_empty_state %}`` at least once."""
        offenders = []
        for path in _list_templates():
            rel = _relative(path)
            if rel in NO_EMPTY_STATE_ALLOWLIST:
                # Page can't reach zero rows — audit excluded.
                continue
            if rel not in PARTIAL_USERS:
                # New list page added since #756 — surface so the test
                # is updated deliberately rather than silently.
                offenders.append(
                    f'{rel}: not in PARTIAL_USERS allow-list — was a new '
                    'studio list page added? Update the audit list.'
                )
                continue
            source = path.read_text()
            if 'studio_empty_state' not in source:
                offenders.append(
                    f'{rel}: missing {{% studio_empty_state %}} — '
                    "still rendering a hand-rolled empty state."
                )
        self.assertEqual(offenders, [], offenders)

    def test_audited_list_pages_have_no_hand_rolled_no_x_found_copy(self):
        """The legacy ``No <x> found.`` / ``No <x> configured yet.``
        copy must be gone from every audited list page (the canonical
        partial renders ``No <plural> yet.`` for fresh-zero and
        ``No <plural> match your filters.`` for filter-zero)."""
        # Patterns that meant a hand-rolled empty cell pre-#756.
        legacy_patterns = [
            r'>No \w+ found\.<',
            r'>No \w+ configured yet\.<',
        ]
        offenders = []
        for path in _list_templates():
            rel = _relative(path)
            if rel not in PARTIAL_USERS:
                continue
            source = path.read_text()
            for pat in legacy_patterns:
                if re.search(pat, source):
                    offenders.append(f'{rel}: matches /{pat}/')
        self.assertEqual(offenders, [], offenders)

    def test_tbody_internal_empty_cells_route_through_partial(self):
        """Any ``<tbody>`` that hosts an inline empty row must do so by
        calling the partial — not by writing ``<tr><td colspan>...``
        directly. The allow-list explicitly exempts pages whose body is
        not a single ``<tbody>``-listed table."""
        # A bare empty <tr><td colspan...> is the legacy hand-rolled
        # filter-zero markup. The partial's filter mode emits a <tr>
        # carrying ``data-testid="studio-empty-state-filter"``, so the
        # canonical pattern is detectable.
        hand_rolled_pattern = re.compile(
            r'<tr>\s*<td[^>]*\bcolspan="\d+"[^>]*>\s*No \w[^<]+</td>\s*</tr>',
            re.IGNORECASE,
        )
        offenders = []
        for path in _list_templates():
            rel = _relative(path)
            if rel in TBODY_ALLOWLIST:
                continue
            if rel not in PARTIAL_USERS:
                continue
            for block in _tbody_blocks(path.read_text()):
                if hand_rolled_pattern.search(block):
                    offenders.append(
                        f'{rel}: hand-rolled inline empty <tr> still '
                        'present in <tbody>. Use '
                        '{% studio_empty_state \'filter\' ... %}.'
                    )
        self.assertEqual(offenders, [], offenders)

    def test_list_page_glob_actually_finds_pages(self):
        """Sanity: if the glob breaks (rename, move), this whole file
        must not silently pass with zero work."""
        templates = _list_templates()
        self.assertGreaterEqual(
            len(templates), 15,
            f'expected 15+ studio list templates, found {len(templates)}',
        )
