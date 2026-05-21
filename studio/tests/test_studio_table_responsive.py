"""Regression tests for Studio table wrappers being mobile-safe.

Issue #760: ``/studio/plans/`` and ``/studio/sprints/`` rendered the
legacy wrapper ``bg-card border border-border rounded-lg overflow-hidden``
instead of the canonical ``LIST_TABLE_WRAPPER_CLASS``
(``studio-responsive-table bg-card border border-border rounded-lg
overflow-x-auto``).

Issue #761 extends the same fix to Studio detail/result pages
(`event_series/detail.html`, `sprints/detail.html`,
`tier_overrides.html`, `users/import_result.html`,
`users/tier_override.html`, `utm_campaigns/detail.html`,
`utm_campaigns/import_result.html`).

At 393x851 the Actions column clipped past the viewport with no scroll
affordance and ``studio-responsive-table``'s row-stacking CSS (defined
in ``templates/studio/base.html`` lines 16-144) never fired, because
the wrapper element did not carry the trigger class.

The fix swaps the wrapper to the canonical class. This test makes sure
every Studio template — list page, detail page, or import result —
keeps both signals on the outer table wrapper:

- ``studio-responsive-table`` so mobile row-stacking activates.
- ``overflow-x-auto`` so even when row-stacking is not in play the
  Actions column stays scrollable instead of being clipped.

The walker explicitly skips ``<table>`` elements whose nearest
ancestor is ``hidden md:block`` — those templates use a separate
``<ul class="md:hidden">`` card list for mobile (e.g.
``courses/access_list.html``, ``courses/enrollments_list.html``) so the
table itself never renders below the ``md`` breakpoint and the legacy
wrapper class on the outer card is harmless.

These are grep-shaped assertions intentionally: they catch drift in any
future Studio page without needing per-page integration coverage.
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

# Tighter pattern used by the cross-template (#761) walker — only the
# legacy ``rounded-lg overflow-hidden`` pair, the ``{% studio_list_class
# 'wrapper' %}`` templatetag, or class strings that already contain
# ``studio-responsive-table``. We deliberately do NOT match plain
# ``rounded-lg overflow-x-auto`` because several pre-#760 Studio
# templates (``events/form.html`` registrations, ``utm_analytics``
# metrics) use that signature with intentionally different responsive
# rules (column scroll only, not row stacking) — those are out of
# scope for #761 and must not be flagged here.
STUDIO_WRAPPER_LINE_PATTERN = re.compile(
    r'<div\s+class="([^"]*?(?:rounded-lg\s+overflow-hidden|'
    r'studio_list_class\s+\'wrapper\'|studio-responsive-table)[^"]*)"'
)

LIST_TEMPLATES = sorted(STUDIO_TEMPLATES_DIR.glob('*/list.html'))

# Every Studio template — used by the broadened walker introduced in
# #761. The walker scans each one for ``<div>`` wrappers that own a
# ``<table>`` element and enforces the responsive class pair.
ALL_STUDIO_TEMPLATES = sorted(STUDIO_TEMPLATES_DIR.rglob('*.html'))


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


def _next_tag_is_table(snippet: str) -> bool:
    """Return True if the first HTML tag in ``snippet`` is ``<table``.

    Skips leading whitespace, HTML/Django comments, ``{% if %}``-style
    template tags, and ``{{ var }}`` interpolations so that a table
    that follows a ``{% csrf_token %}`` or ``{% if foo %}`` opener still
    counts as the wrapper's first child.
    """
    cursor = 0
    while cursor < len(snippet):
        ch = snippet[cursor]
        if ch.isspace():
            cursor += 1
            continue
        # Skip Django tag block: {% ... %}
        if snippet.startswith('{%', cursor):
            end = snippet.find('%}', cursor)
            if end == -1:
                return False
            cursor = end + 2
            continue
        # Skip Django variable: {{ ... }}
        if snippet.startswith('{{', cursor):
            end = snippet.find('}}', cursor)
            if end == -1:
                return False
            cursor = end + 2
            continue
        if ch == '<':
            return snippet.startswith('<table', cursor)
        # Anything else (text content, attribute leftovers) means this
        # div doesn't immediately contain a table.
        return False
    return False


def _clean_snippet(snippet: str) -> str:
    """Strip HTML and Django comments out of a forward-looking snippet."""
    cleaned = re.sub(r'<!--.*?-->', '', snippet, flags=re.DOTALL)
    cleaned = re.sub(r'{#.*?#}', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(
        r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}',
        '', cleaned, flags=re.DOTALL,
    )
    return cleaned


def _table_wrapper_classes_for(template_path: Path) -> list[str]:
    """Return wrapper class strings on ``<div>`` elements that match the
    legacy-or-canonical wrapper signature AND own a ``<table>``.

    This is the original (pre-#761) walker used by the list-page tests.
    It only inspects wrappers whose class attribute contains either the
    legacy ``rounded-lg overflow-hidden`` pair, the canonical
    ``rounded-lg overflow-x-auto`` pair, or the ``{% studio_list_class
    'wrapper' %}`` templatetag.
    """
    source = template_path.read_text()
    classes = []
    for match in WRAPPER_LINE_PATTERN.finditer(source):
        class_attr = match.group(1)
        tail = source[match.end():]
        snippet = tail[:600]
        if '<table' in snippet or '{% include' in snippet and '<table' in tail[:1500]:
            classes.append(_render_wrapper_class(class_attr))
    return classes


def _all_table_wrappers_for(template_path: Path) -> list[tuple[str, str]]:
    """Walk every ``<div>`` matching the Studio responsive-wrapper
    signature in a template and, for each one that owns a ``<table>``,
    return ``(rendered_class_attr, raw_class_attr)``.

    A wrapper "owns" a table when, scanning forward from the wrapper's
    opening tag, we reach a ``<table>`` before any nested ``<div
    class="...hidden md:block...">``. The ``hidden md:block`` short
    circuit guards against false positives from the
    desktop-table-plus-mobile-card pattern used by
    ``courses/access_list.html`` and ``courses/enrollments_list.html`` —
    those templates wrap their desktop table in an inner ``<div
    class="hidden md:block">`` so the table never renders below the
    ``md`` breakpoint, and the legacy-overflow-hidden outer card it
    lives in is harmless on mobile.

    The signature filter (legacy ``rounded-lg overflow-hidden``,
    canonical ``{% studio_list_class 'wrapper' %}``, already-fixed
    ``studio-responsive-table``) keeps the walker focused on the
    wrappers #760/#761 own. Bare ``<div class="overflow-x-auto">``
    wrappers (e.g. ``users/detail.html`` course context,
    ``workshops/detail.html`` pages, ``utm_analytics/link_detail.html``
    visits) are out of scope for this issue and not flagged here.
    """
    source = template_path.read_text()
    results: list[tuple[str, str]] = []
    for match in STUDIO_WRAPPER_LINE_PATTERN.finditer(source):
        raw_class_attr = match.group(1)
        if 'hidden md:block' in raw_class_attr:
            continue
        # Advance past the rest of the wrapper's own opening tag.
        tail = source[match.end():]
        gt = tail.find('>')
        if gt == -1:
            continue
        body = _clean_snippet(tail[gt + 1:gt + 1 + 1500])
        table_at = body.find('<table')
        if table_at == -1:
            continue
        # If a nested ``hidden md:block`` div opens before the table,
        # this wrapper is the outer card of the desktop-table /
        # mobile-card pattern — the table inside is gated on the ``md``
        # breakpoint and never renders on Pixel-7, so the legacy class
        # on the outer card is harmless. Skip it.
        gated_at = re.search(r'<div\s+class="[^"]*hidden md:block[^"]*"', body)
        if gated_at and gated_at.start() < table_at:
            continue
        results.append((_render_wrapper_class(raw_class_attr), raw_class_attr))
    return results


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


class StudioAllTemplatesWrapperResponsiveTest(SimpleTestCase):
    """Every legacy/canonical-signature wrapper that owns a ``<table>``
    in any Studio template — list, detail or import result — must
    carry the responsive class pair.

    Introduced in #761 to lock the swap in across detail/result pages
    after #760 fixed list pages. Wrappers nested inside a ``<div
    class="hidden md:block">`` (the desktop-table-plus-mobile-card
    pattern used by ``courses/access_list.html`` and
    ``courses/enrollments_list.html``) are skipped because the table
    never renders below the ``md`` breakpoint.
    """

    def test_walker_finds_a_meaningful_set_of_table_wrappers(self):
        """Sanity check: future refactors that rename or move templates
        must NOT silently degrade this test to zero coverage."""
        total = 0
        for template in ALL_STUDIO_TEMPLATES:
            total += len(_all_table_wrappers_for(template))
        # 15 is comfortably below the real count today (~20+) but high
        # enough to catch accidental collapse of the walker.
        self.assertGreater(
            total, 15,
            f'walker found only {total} table wrappers — did a regex break?'
        )

    def test_no_studio_template_uses_overflow_hidden_on_table_wrapper(self):
        """``overflow-hidden`` on a wrapper that owns a ``<table>`` clips
        the right-most columns past the Pixel-7 viewport."""
        offenders = []
        for template in ALL_STUDIO_TEMPLATES:
            for rendered, raw in _all_table_wrappers_for(template):
                if 'overflow-hidden' in rendered:
                    offenders.append(
                        f'{template.relative_to(REPO_ROOT)}: {raw}'
                    )
        self.assertEqual(offenders, [], offenders)

    def test_every_studio_table_wrapper_has_studio_responsive_table(self):
        """Mobile row-stacking CSS in ``templates/studio/base.html``
        only activates when the wrapper carries ``studio-responsive-table``."""
        missing = []
        for template in ALL_STUDIO_TEMPLATES:
            for rendered, raw in _all_table_wrappers_for(template):
                if 'studio-responsive-table' not in rendered:
                    missing.append(
                        f'{template.relative_to(REPO_ROOT)}: {raw}'
                    )
        self.assertEqual(missing, [], missing)

    def test_every_studio_table_wrapper_has_overflow_x_auto(self):
        """Even where row-stacking is not in play (very wide desktop)
        the Actions column must remain reachable by horizontal scroll."""
        missing = []
        for template in ALL_STUDIO_TEMPLATES:
            for rendered, raw in _all_table_wrappers_for(template):
                if 'overflow-x-auto' not in rendered:
                    missing.append(
                        f'{template.relative_to(REPO_ROOT)}: {raw}'
                    )
        self.assertEqual(missing, [], missing)

    def test_detail_pages_fixed_in_761_use_canonical_wrapper(self):
        """Pin the specific surfaces enumerated in #761 — guards against
        any future re-introduction of the legacy class on these files."""
        in_scope = [
            'event_series/detail.html',
            'sprints/detail.html',
            'tier_overrides.html',
            'users/import_result.html',
            'users/tier_override.html',
            'utm_campaigns/detail.html',
            'utm_campaigns/import_result.html',
        ]
        for rel in in_scope:
            with self.subTest(template=rel):
                path = STUDIO_TEMPLATES_DIR / rel
                source = path.read_text()
                self.assertNotIn(
                    'bg-card border border-border rounded-lg overflow-hidden',
                    source,
                    f'{rel} still carries the legacy overflow-hidden wrapper',
                )
                wrappers = _all_table_wrappers_for(path)
                self.assertTrue(
                    wrappers,
                    f'{rel} had no detectable table wrapper after the swap',
                )
                for rendered, _ in wrappers:
                    self.assertIn('studio-responsive-table', rendered)
                    self.assertIn('overflow-x-auto', rendered)
