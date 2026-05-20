"""Regression tests for the shared Studio list filter-bar partial.

Issue #754 — class 8 of the #747 audit. Five list pages used to inline
their own ``<form method="get">`` search bar. They now go through the
canonical ``studio_list_filter`` inclusion tag so the search input,
status dropdown, and Search button stay in lock-step across Studio.

This test file pins down three things that the audit cared about:

1. The three pages that had a search input — ``campaigns/list.html``,
   ``recordings/list.html``, ``downloads/list.html`` — must call the
   partial and no longer carry an inline ``<form method="get">`` with
   ``name="q"`` inside.
2. ``STATUS_OPTIONS`` exposes a ``campaign`` key with ``draft / sending /
   sent`` so the campaigns page renders the right dropdown via the tag.
3. The tag's ``status_kind=None`` search-only mode actually hides the
   status ``<select>`` — render the tag in a stub template and assert
   the dropdown is absent.
"""

import re
from pathlib import Path

from django.template import Context, Template
from django.test import TestCase

from studio.templatetags.studio_filters import STATUS_OPTIONS

REPO_ROOT = Path(__file__).resolve().parents[2]

# Template paths whose previous bespoke search form should now route
# through the shared partial.
CONSOLIDATED_TEMPLATE_PATHS = [
    'templates/studio/campaigns/list.html',
    'templates/studio/recordings/list.html',
    'templates/studio/downloads/list.html',
]

# Detects an inline ``<form method="get" ...> ... name="q" ...`` pair on
# one or two consecutive lines — the shape of the bespoke search forms
# this issue removes. The shared partial lives in its own file so this
# pattern only matches the inlined duplicates.
INLINE_SEARCH_FORM_RE = re.compile(
    r'<form\s+method="get"[^>]*>[^<]*<input[^>]*name="q"',
    re.DOTALL,
)


def _template_source(relative_path):
    return (REPO_ROOT / relative_path).read_text()


class StudioListFilterConsolidationTest(TestCase):
    """The three pages now defer to ``{% studio_list_filter %}``."""

    def test_targets_call_the_shared_partial(self):
        for path in CONSOLIDATED_TEMPLATE_PATHS:
            with self.subTest(path=path):
                source = _template_source(path)
                self.assertIn(
                    '{% studio_list_filter',
                    source,
                    msg=(
                        f"{path} should call {{% studio_list_filter %}} "
                        "instead of inlining a search form."
                    ),
                )

    def test_targets_drop_inline_search_form(self):
        for path in CONSOLIDATED_TEMPLATE_PATHS:
            with self.subTest(path=path):
                source = _template_source(path)
                match = INLINE_SEARCH_FORM_RE.search(source)
                self.assertIsNone(
                    match,
                    msg=(
                        f"{path} still has an inline <form method=\"get\"> "
                        'containing name="q"; the search form must go '
                        'through the shared partial.'
                    ),
                )


class StudioListFilterStatusOptionsTest(TestCase):
    """``STATUS_OPTIONS`` exposes the new ``campaign`` set."""

    def test_status_options_has_campaign_key(self):
        self.assertIn('campaign', STATUS_OPTIONS)

    def test_campaign_status_options_match_email_campaign_states(self):
        self.assertEqual(
            STATUS_OPTIONS['campaign'],
            [
                ('draft', 'Draft'),
                ('sending', 'Sending'),
                ('sent', 'Sent'),
            ],
        )


class StudioListFilterSearchOnlyModeTest(TestCase):
    """``status_kind=None`` hides the status dropdown."""

    def _render(self, **kwargs):
        defaults = {
            'search': '',
            'status_filter': '',
            'placeholder': 'Search downloads...',
            'status_kind': None,
            'auto_submit': False,
        }
        defaults.update(kwargs)
        template = Template(
            "{% load studio_filters %}"
            "{% studio_list_filter search status_filter placeholder "
            "status_kind auto_submit %}"
        )
        return template.render(Context(defaults))

    def test_search_only_mode_omits_status_select(self):
        rendered = self._render()
        self.assertNotIn('name="status"', rendered)
        self.assertNotIn('data-testid="studio-status-filter"', rendered)
        # The search input still renders so the page is usable.
        self.assertIn('name="q"', rendered)
        self.assertIn('placeholder="Search downloads..."', rendered)

    def test_default_mode_still_shows_status_select(self):
        rendered = self._render(status_kind='publication')
        self.assertIn('name="status"', rendered)
        self.assertIn('data-testid="studio-status-filter"', rendered)
        # Publication labels still come through unchanged.
        self.assertIn('>Published<', rendered)


class StudioListFilterContainerSpacingTest(TestCase):
    """Container spacing on the partial and the two exceptions."""

    def test_partial_uses_mb6_canonical_spacing(self):
        source = _template_source(
            'templates/studio/includes/list_filter_form.html'
        )
        # The first wrapper div picks up the canonical mb-6 spacing.
        self.assertIn(
            'class="mb-6 flex flex-wrap items-center gap-3"',
            source,
        )
        # The pre-issue ``mb-3 md:mb-6`` variant is gone.
        self.assertNotIn('mb-3 md:mb-6', source)

    def test_imports_filter_container_uses_canonical_spacing(self):
        source = _template_source('templates/studio/imports/list.html')
        self.assertIn(
            '<form method="get" class="mb-6 flex flex-wrap items-center gap-3">',
            source,
        )
        # The previous ``items-end gap-4`` variant must be gone.
        self.assertNotIn('items-end gap-4', source)

    def test_utm_archive_toggle_container_uses_canonical_spacing(self):
        source = _template_source(
            'templates/studio/utm_campaigns/list.html'
        )
        self.assertIn(
            '<div class="mb-6 flex flex-wrap items-center gap-2">',
            source,
        )
        # The previous ``mb-4 ... space-x-2`` variant must be gone.
        self.assertNotIn(
            '<div class="mb-4 flex items-center space-x-2">',
            source,
        )
