"""Regression tests for Studio list-page header row consistency.

Issue #752 (from the #747 audit) unifies the top header row of every
Studio ``*/list.html`` template so they all share the same responsive
container, spacing, bottom margin, CTA icon, and label casing
convention.

Canonical shape, documented in ``_docs/design-system.md`` §Studio
list-page header row:

- Outer container: ``flex flex-col sm:flex-row sm:items-center
  justify-between gap-2 mb-8`` (mobile column, desktop row).
- Inner action container (when there are multiple header buttons):
  ``flex items-center gap-2`` — no ``space-x-*``, no ``gap-3``.
- Header bottom margin is ``mb-8`` everywhere (no ``mb-4 md:mb-8``,
  no ``mb-6``).
- Every primary header CTA carries a Lucide ``h-4 w-4`` leading icon.
- Primary CTA labels use sentence case ``New <noun>`` for creation
  and ``Import <noun>`` / ``Export <noun>`` / ``Re-sync <noun>`` for
  transformations. No ``Add`` or ``Create`` verbs.

The assertions below are grep-shaped over every ``templates/studio/
*/list.html`` so future drift on any list page is caught at test time.

Exempt pages (different layout role, called out in
``_docs/design-system.md``):

- ``crm/list.html`` — wider filter bar, ``mb-6``.
- ``email_templates/list.html`` — no right-side header CTA.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_TEMPLATES_DIR = REPO_ROOT / 'templates' / 'studio'

# Pages that legitimately diverge from the canonical list-header shape.
EXEMPT_LIST_TEMPLATES = {
    'crm/list.html',
    'email_templates/list.html',
}

CANONICAL_HEADER_CLASSES = (
    'flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-8'
)

# Tokens that, if seen inside the header ``<div>``, indicate the page
# is still using the old desktop-only or mismatched-gap shape.
FORBIDDEN_HEADER_TOKENS = (
    # Desktop-only flex with no mobile column fallback.
    'flex items-center justify-between',
    # space-x utilities are replaced by gap-* uniformly on the header row.
    'space-x-',
    # mb-4 md:mb-8 is the old responsive bottom-margin pattern we replaced.
    'mb-4 md:mb-8',
)

# Primary CTAs sit inside the header ``<div>`` and use the canonical
# accent fill (``bg-accent text-accent-foreground``) plus button shape
# (``px-4 py-2``). This matches the existing CTA pattern in
# ``test_studio_list_cta_consistency.py`` for the primary case.
PRIMARY_CTA_FILL_TOKEN = 'bg-accent text-accent-foreground'

# Lucide leading-icon shape. ``h-4 w-4`` is the canonical button-icon size
# (design-system.md §Iconography). Tolerate either order (``h-4 w-4`` or
# ``w-4 h-4``) for backwards compatibility with existing button markup.
LUCIDE_LEAD_ICON_PATTERN = re.compile(
    r'<i\s+data-lucide="[^"]+"[^>]*class="[^"]*\b(?:h-4 w-4|w-4 h-4)\b[^"]*"'
)


def _iter_list_templates() -> list[Path]:
    return sorted(STUDIO_TEMPLATES_DIR.glob('*/list.html'))


def _relative(template: Path) -> str:
    return str(template.relative_to(STUDIO_TEMPLATES_DIR)).replace('\\', '/')


def _non_exempt_list_templates() -> list[Path]:
    return [t for t in _iter_list_templates() if _relative(t) not in EXEMPT_LIST_TEMPLATES]


def _header_div_block(source: str) -> str | None:
    """Return the source of the top header ``<div>`` block on a list page.

    The studio base wraps every list page's ``studio_content`` block.
    The first ``<div>`` after ``{% block studio_content %}`` is the
    header row. We extract from that ``<div ...>`` opening tag up to
    its matching ``</div>`` so the assertions below scan only the
    header row, not the body table or empty-state card.
    """
    block_match = re.search(r'{%\s*block\s+studio_content\s*%}', source)
    if not block_match:
        return None
    start = block_match.end()
    # Skip blank lines / comments to find the first ``<div`` opening.
    first_div = re.search(r'<div\b[^>]*>', source[start:])
    if not first_div:
        return None
    open_offset = start + first_div.start()

    # Walk the source counting nested ``<div>`` opens to find the
    # matching close. This is a tiny single-pass scanner; it does not
    # need to be a full HTML parser for these templates.
    depth = 0
    pos = open_offset
    tag_pattern = re.compile(r'<(/?)div\b[^>]*>')
    while pos < len(source):
        m = tag_pattern.search(source, pos)
        if not m:
            return None
        if m.group(1) == '/':
            depth -= 1
            if depth == 0:
                return source[open_offset:m.end()]
        else:
            depth += 1
        pos = m.end()
    return None


class StudioListHeaderRowConsistencyTest(SimpleTestCase):
    """Every Studio list-page header row must follow the canonical shape."""

    def test_discovery_finds_at_least_fifteen_list_templates(self):
        # Sanity-check: the glob must find the bulk of the studio list
        # pages so a rename can't silently green the suite.
        templates = _iter_list_templates()
        self.assertGreaterEqual(
            len(templates),
            15,
            f'expected >=15 list templates, found {len(templates)}: '
            f'{[_relative(t) for t in templates]}',
        )

    def test_every_non_exempt_header_uses_canonical_container_class(self):
        """Header ``<div>`` must carry the canonical responsive class string."""
        offenders = []
        for template in _non_exempt_list_templates():
            source = template.read_text()
            head = '\n'.join(source.splitlines()[:20])
            if CANONICAL_HEADER_CLASSES not in head:
                offenders.append(_relative(template))
        self.assertEqual(
            offenders,
            [],
            f'these list templates lack the canonical header container '
            f'class string ({CANONICAL_HEADER_CLASSES!r}) in their first '
            f'20 lines: {offenders}',
        )

    def test_no_header_uses_desktop_only_flex_container(self):
        """``flex items-center justify-between`` skips the mobile column fallback."""
        offenders = []
        for template in _non_exempt_list_templates():
            source = template.read_text()
            header = _header_div_block(source) or ''
            for token in FORBIDDEN_HEADER_TOKENS:
                if token in header:
                    offenders.append(f'{_relative(template)}: forbidden token {token!r}')
        self.assertEqual(
            offenders,
            [],
            f'these list templates use a forbidden header-row class token '
            f'(see _docs/design-system.md §Studio list-page header row): '
            f'{offenders}',
        )

    def test_every_primary_cta_has_a_lucide_lead_icon(self):
        """Header primary CTA must carry a leading Lucide icon (h-4 w-4)."""
        offenders = []
        for template in _non_exempt_list_templates():
            source = template.read_text()
            header = _header_div_block(source) or ''
            if PRIMARY_CTA_FILL_TOKEN not in header:
                # This list page has no primary accent CTA in the header
                # (e.g. articles, courses, projects, recordings,
                # downloads, notifications). That is allowed — the header
                # may show just a title + subtitle, or carry secondary
                # bordered actions only (users page).
                continue
            if not LUCIDE_LEAD_ICON_PATTERN.search(header):
                offenders.append(_relative(template))
        self.assertEqual(
            offenders,
            [],
            f'these list templates have a primary header CTA without a '
            f'Lucide h-4 w-4 leading icon: {offenders}',
        )

    def test_no_primary_cta_uses_add_or_create_verb(self):
        """Primary CTA labels use ``New <noun>``, not ``Add`` / ``Create``."""
        offenders = []
        # Look for label text inside header primary CTAs. The label sits
        # in a ``<span>...</span>`` after the leading icon. Anchors that
        # carry the canonical accent fill are header primary CTAs.
        anchor_pattern = re.compile(
            r'<a\b[^>]*class="[^"]*bg-accent[^"]*text-accent-foreground[^"]*"[^>]*>'
            r'(.*?)</a>',
            re.DOTALL,
        )
        for template in _non_exempt_list_templates():
            source = template.read_text()
            header = _header_div_block(source) or ''
            for body in anchor_pattern.findall(header):
                # Extract visible label text — strip tags, collapse whitespace.
                stripped = re.sub(r'<[^>]+>', ' ', body)
                label = re.sub(r'\s+', ' ', stripped).strip()
                if not label:
                    continue
                lowered = label.lower()
                # Allow only ``new <noun>``, ``import <noun>``,
                # ``export <noun>``, ``re-sync <noun>`` openings.
                if re.match(r'^(add|create)\b', lowered):
                    offenders.append(f'{_relative(template)}: {label!r}')
        self.assertEqual(
            offenders,
            [],
            f'these list templates use ``Add`` or ``Create`` verbs on a '
            f'primary header CTA (use ``New <noun>`` instead): {offenders}',
        )

    def test_primary_cta_labels_match_sentence_case_convention(self):
        """``New <noun>`` keeps the noun in lower case (sentence case).

        ``New Campaign`` -> ``New campaign``. Domain initialisms such as
        ``CSV`` legitimately preserve their casing (``Export CSV``) and
        are allowed.
        """
        offenders = []
        anchor_pattern = re.compile(
            r'<a\b[^>]*class="[^"]*bg-accent[^"]*text-accent-foreground[^"]*"[^>]*>'
            r'(.*?)</a>',
            re.DOTALL,
        )
        for template in _non_exempt_list_templates():
            source = template.read_text()
            header = _header_div_block(source) or ''
            for body in anchor_pattern.findall(header):
                stripped = re.sub(r'<[^>]+>', ' ', body)
                label = re.sub(r'\s+', ' ', stripped).strip()
                if not label:
                    continue
                m = re.match(r'^(New|Import|Export|Re-sync)\s+(.+)$', label)
                if not m:
                    offenders.append(
                        f'{_relative(template)}: {label!r} (must start '
                        f'with ``New``, ``Import``, ``Export``, or ``Re-sync``)'
                    )
                    continue
                noun = m.group(2)
                # First word of the noun must be lower-case (sentence
                # case) — unless it is an all-caps domain initialism
                # such as ``CSV`` or ``UTM``.
                first_word = noun.split(' ', 1)[0]
                if first_word != first_word.lower() and not first_word.isupper():
                    offenders.append(
                        f'{_relative(template)}: {label!r} (noun should '
                        f'be sentence case, not {first_word!r})'
                    )
        self.assertEqual(
            offenders,
            [],
            f'these list templates have CTA labels that violate the '
            f'sentence-case ``New <noun>`` convention: {offenders}',
        )

    def test_sprint_status_badge_uses_centralised_helper(self):
        """``sprints/list.html`` renders status via ``studio_status_badge``.

        Before issue #752 the sprint list hand-rolled a neutral
        ``bg-secondary`` pill that made Draft and Active look identical.
        The fix is to call the centralised ``studio_status_badge``
        template tag so the new ``active`` -> green and
        ``completed`` -> grey-muted mapping in
        ``STATUS_BADGE_CLASSES`` (see ``studio/templatetags/
        studio_filters.py``) applies uniformly.
        """
        source = (STUDIO_TEMPLATES_DIR / 'sprints' / 'list.html').read_text()
        self.assertIn('{% studio_status_badge sprint.status', source)
        # Defensive: the old hand-rolled pill is gone.
        self.assertNotIn(
            '<span class="text-xs px-2 py-1 rounded-full bg-secondary text-foreground">'
            '{{ sprint.get_status_display }}',
            source,
        )

    def test_status_badge_classes_include_active_and_completed(self):
        """``STATUS_BADGE_CLASSES`` exposes the new sprint palette."""
        from studio.templatetags.studio_filters import STATUS_BADGE_CLASSES

        # ``active`` is the new live state — same green as ``published``
        # so the sprint pill reads as in-progress, not neutral.
        self.assertEqual(
            STATUS_BADGE_CLASSES['active'],
            'bg-green-500/20 text-green-400',
        )
        # ``completed`` (alias of "archived") stays grey/muted.
        self.assertEqual(
            STATUS_BADGE_CLASSES['completed'],
            'bg-secondary text-muted-foreground',
        )

    def test_users_list_header_actions_have_upload_download_icons(self):
        """Both Import contacts and Export CSV carry a lead icon now."""
        source = (STUDIO_TEMPLATES_DIR / 'users' / 'list.html').read_text()
        header = _header_div_block(source) or ''
        # Import contacts -> upload icon.
        self.assertIn('data-lucide="upload"', header)
        self.assertIn('Import contacts', header)
        # Export CSV -> download icon.
        self.assertIn('data-lucide="download"', header)
        self.assertIn('Export CSV', header)
        # Neither button is the primary accent CTA (PM ruling: users
        # have no creation action, so both stay bordered-secondary).
        self.assertNotIn('bg-accent text-accent-foreground', header)
