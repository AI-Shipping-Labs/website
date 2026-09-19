"""Static contracts for Studio pages using their indexed header owner."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_TEMPLATES = REPO_ROOT / 'templates' / 'studio'
PACKAGE_SETTINGS_TEMPLATE = REPO_ROOT / 'templates' / 'community_base' / 'config' / 'settings.html'

# Issue #1434 baseline, derived from origin/main 5589a718. Values are the
# current number of header blocks in each non-conforming full page. This is a
# positive debt inventory, not permission for new pages to omit the owner.
KNOWN_EXCEPTIONS = {
    'announcement/edit.html': 0,
    'api_tokens/create.html': 0,
    'api_tokens/created.html': 0,
    'assistant.html': 0,
    'books/form.html': 2,
    'call_hosts/form.html': 2,
    'campaigns/recipients.html': 0,
    'content_sources/create.html': 0,
    'courses/access_list.html': 0,
    'courses/enrollments_list.html': 0,
    'courses/peer_reviews.html': 0,
    'crm/slack_ingest.html': 0,
    'dashboard.html': 0,
    'email_templates/edit.html': 0,
    'email_templates/list.html': 0,
    'event_series/form.html': 0,
    'events/duplicates.html': 0,
    'events/form.html': 4,
    'hosts/form.html': 0,
    'imports/detail.html': 0,
    'imports/new.html': 0,
    'marketing_pages/form.html': 2,
    'maven_events/detail.html': 0,
    'personas/form.html': 2,
    'plans/edit.html': 0,
    'plans/form.html': 0,
    'plans/move_unfinished.html': 0,
    'questionnaires/form.html': 2,
    'questionnaires/question_form.html': 0,
    'questionnaires/response_question_form.html': 0,
    'redirects/form.html': 2,
    'signup_analytics/dashboard.html': 0,
    'sprints/enroll.html': 0,
    'sprints/form.html': 2,
    'sprints/plan_request_prepare.html': 0,
    'tier_overrides.html': 0,
    'triggers/delivery_list.html': 0,
    'triggers/emission_list.html': 0,
    'triggers/subscription_form.html': 0,
    'triggers/widget_form.html': 0,
    'users/create.html': 0,
    'users/created.html': 0,
    'users/import.html': 0,
    'users/import_result.html': 0,
    'users/merge.html': 0,
    'users/note_form.html': 2,
    'users/tier_override.html': 0,
    'utm_analytics/campaign_detail.html': 0,
    'utm_analytics/dashboard.html': 0,
    'utm_analytics/link_detail.html': 0,
    'utm_campaigns/form.html': 2,
    'utm_campaigns/import.html': 0,
    'utm_campaigns/import_result.html': 0,
    'worker.html': 0,
}

# An exception may be removed or its count lowered, but it may never grow
# beyond this implementation-time ceiling. Keeping an independent frozen
# snapshot lets cleanup tighten KNOWN_EXCEPTIONS without turning deleted debt
# back on; adding a key to KNOWN_EXCEPTIONS alone therefore cannot pass.
EXCEPTION_CEILING = {
    path: int(count)
    for path, count in (
        line.rsplit('=', 1)
        for line in """announcement/edit.html=0
api_tokens/create.html=0
api_tokens/created.html=0
assistant.html=0
books/form.html=2
call_hosts/form.html=2
campaigns/form.html=0
campaigns/recipients.html=0
content_sources/create.html=0
courses/access_list.html=0
courses/enrollments_list.html=0
courses/peer_reviews.html=0
crm/slack_ingest.html=0
dashboard.html=0
email_templates/edit.html=0
email_templates/list.html=0
event_series/form.html=0
events/duplicates.html=0
events/form.html=4
hosts/form.html=0
imports/detail.html=0
imports/new.html=0
marketing_pages/form.html=2
maven_events/detail.html=0
personas/form.html=2
plans/edit.html=0
plans/form.html=0
plans/move_unfinished.html=0
questionnaires/form.html=2
questionnaires/question_form.html=0
questionnaires/response_question_form.html=0
redirects/form.html=2
signup_analytics/dashboard.html=0
sprints/enroll.html=0
sprints/form.html=2
sprints/plan_request_prepare.html=0
tier_overrides.html=0
triggers/delivery_list.html=0
triggers/emission_list.html=0
triggers/subscription_form.html=0
triggers/widget_form.html=0
users/create.html=0
users/created.html=0
users/import.html=0
users/import_result.html=0
users/merge.html=0
users/note_form.html=2
users/tier_override.html=0
utm_analytics/campaign_detail.html=0
utm_analytics/dashboard.html=0
utm_analytics/link_detail.html=0
utm_campaigns/form.html=2
utm_campaigns/import.html=0
utm_campaigns/import_result.html=0
worker.html=0""".splitlines()
    )
}

ACTIONLESS_HEADERS = {
    'articles/list.html',
    'courses/list.html',
    'downloads/list.html',
    'email_log/list.html',
    'maven_events/list.html',
    'notifications/list.html',
    'projects/list.html',
    'recordings/list.html',
    'ses_events/list.html',
}

FORBIDDEN_HEADER_TOKENS = (
    'justify-between',
    'sm:flex-row',
    'sm:justify-end',
    'shrink-0',
    'space-x-',
)

# Split into two passes on purpose. The four block regions below genuinely
# span lines, so they need `re.DOTALL`. A `{# #}` comment does not: Django's
# `tag_re` lexes it only when the closer sits on the opening line, so a `{#`
# that closes later is plain rendered text. Blanking such a region would exempt
# a real, visible header row from these rules. Keep the flag off the comment
# pattern — `content/tests/test_template_comment_lint.py` guarantees the tree
# carries no multi-line `{# #}` to begin with.
IGNORED_BLOCK_REGIONS = re.compile(
    r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}'
    r'|<!--.*?-->'
    r'|<script\b.*?</script\s*>'
    r'|<style\b.*?</style\s*>',
    re.DOTALL | re.IGNORECASE,
)
IGNORED_LINE_COMMENTS = re.compile(r'{#[^\n]*?#}')
FULL_PAGE_EXTENDS = re.compile(
    r'{%\s*extends\s+["\']studio/base\.html["\']\s*%}',
    re.DOTALL,
)
# The opening tag must not match across a newline: Django would not lex such a
# tag either, so a `{% studio_header_actions` whose `%}` lands on a later line
# is rendered text, not a header owner. `[^\n]` keeps the opener single-line
# while the surrounding `re.DOTALL` stays on for `body`, which legitimately
# spans lines. Do not swap these back to `.` — see `_mask_ignored_regions`.
HEADER_OPEN = re.compile(r'{%[^\S\n]*studio_header_actions\b[^\n]*?%}')
HEADER_BLOCK = re.compile(
    r'{%[^\S\n]*studio_header_actions\b(?P<opening>[^\n]*?)%}'
    r'(?P<body>.*?)'
    r'{%\s*endstudio_header_actions\s*%}',
    re.DOTALL,
)
MAX_DIAGNOSTICS = 25
HEADER_OWNER = '{% studio_header_actions %}'


def _blank(match: re.Match) -> str:
    return re.sub(r'[^\n]', ' ', match.group(0))


def _mask_ignored_regions(source: str) -> str:
    """Hide source-only lookalikes while preserving their line positions.

    Two passes because the constructs differ: the block regions span lines and
    are matched with `re.DOTALL`, while `{# #}` comments are single-line by
    Django's own tokenizer and are matched without it. A multi-line `{#` is
    therefore left visible, because Django renders it.
    """

    masked = IGNORED_BLOCK_REGIONS.sub(_blank, source)
    return IGNORED_LINE_COMMENTS.sub(_blank, masked)


def _line_number(source: str, offset: int) -> int:
    return source.count('\n', 0, offset) + 1


def _diagnostic(
    rule: str,
    path: str,
    line: int,
    allowed: int,
    actual: int,
    remediation: str,
) -> str:
    return (
        f'rule={rule} path={path} line={line} allowed={allowed} '
        f'actual={actual} remediation={remediation}'
    )


def _bounded(diagnostics: list[str]) -> list[str]:
    ordered = sorted(set(diagnostics))
    if len(ordered) <= MAX_DIAGNOSTICS:
        return ordered
    omitted = len(ordered) - MAX_DIAGNOSTICS
    return ordered[:MAX_DIAGNOSTICS] + [
        _diagnostic(
            'studio-header-diagnostic-limit',
            'templates/studio',
            1,
            MAX_DIAGNOSTICS,
            len(ordered),
            f'fix-listed-violations-and-rerun ({omitted} omitted)',
        ),
    ]


def _discover_full_page_sources(root: Path = STUDIO_TEMPLATES) -> dict[str, str]:
    """Discover full pages recursively from the working tree, without Git."""

    pages = {}
    for path in root.rglob('*.html'):
        source = path.read_text(encoding='utf-8')
        if FULL_PAGE_EXTENDS.search(_mask_ignored_regions(source)):
            pages[path.relative_to(root).as_posix()] = source
    return pages


def _scan_header_sources(
    sources: Mapping[str, str],
    exceptions: Mapping[str, int] = KNOWN_EXCEPTIONS,
    ceiling: Mapping[str, int] = EXCEPTION_CEILING,
) -> list[str]:
    """Return bounded, actionable ownership-ratchet diagnostics."""

    diagnostics = []
    for relative, allowed_count in sorted(exceptions.items()):
        ceiling_count = ceiling.get(relative)
        if ceiling_count is None:
            diagnostics.append(
                _diagnostic(
                    'studio-header-exception-path-ceiling',
                    relative,
                    1,
                    0,
                    1,
                    f'use-{HEADER_OWNER}-instead-of-adding-an-exception',
                ),
            )
        elif allowed_count > ceiling_count:
            diagnostics.append(
                _diagnostic(
                    'studio-header-exception-count-ceiling',
                    relative,
                    1,
                    ceiling_count,
                    allowed_count,
                    f'use-exactly-one-{HEADER_OWNER}',
                ),
            )

    discovered = {}
    for relative, source in sorted(sources.items()):
        masked = _mask_ignored_regions(source)
        if not FULL_PAGE_EXTENDS.search(masked):
            continue
        discovered[relative] = masked
        openings = list(HEADER_OPEN.finditer(masked))
        matches = list(HEADER_BLOCK.finditer(masked))
        actual = len(matches)
        line = _line_number(masked, openings[0].start()) if openings else 1
        if actual == 1:
            if relative in exceptions:
                diagnostics.append(
                    _diagnostic(
                        'studio-header-stale-exception',
                        relative,
                        line,
                        0,
                        1,
                        'remove-path-from-KNOWN_EXCEPTIONS',
                    ),
                )
            continue

        allowed = exceptions.get(relative)
        if allowed is None:
            diagnostics.append(
                _diagnostic(
                    'studio-header-owner',
                    relative,
                    line,
                    1,
                    actual,
                    f'use-exactly-one-{HEADER_OWNER}',
                ),
            )
        elif actual != allowed:
            diagnostics.append(
                _diagnostic(
                    'studio-header-exception-budget',
                    relative,
                    line,
                    allowed,
                    actual,
                    f'reduce-debt-and-lower-KNOWN_EXCEPTIONS-or-use-{HEADER_OWNER}',
                ),
            )

    for relative in sorted(set(exceptions) - set(discovered)):
        diagnostics.append(
            _diagnostic(
                'studio-header-stale-exception-path',
                relative,
                1,
                0,
                1,
                'remove-path-from-KNOWN_EXCEPTIONS',
            ),
        )
    return _bounded(diagnostics)


def _source(relative: str) -> str:
    return (STUDIO_TEMPLATES / relative).read_text(encoding='utf-8')


def _conforming_pages() -> dict[str, str]:
    return {
        relative: source
        for relative, source in _discover_full_page_sources().items()
        if relative not in KNOWN_EXCEPTIONS
    }


@tag('core')
class IgnoredRegionMaskingTest(SimpleTestCase):
    """The masking must mirror Django's tokenizer, not a permissive superset."""

    def test_multiline_comment_region_stays_visible(self):
        source = (
            '{# note about the row\n'
            '<div>visible-marker</div>\n'
            'end of note #}\n'
        )

        masked = _mask_ignored_regions(source)

        # Django never lexes that comment, so the markup inside it really
        # renders and the header rules must still evaluate it.
        self.assertIn('visible-marker', masked)

    def test_single_line_comment_is_still_masked(self):
        masked = _mask_ignored_regions('{# scrubbed-marker #}\n<p>kept</p>')

        self.assertNotIn('scrubbed-marker', masked)
        self.assertIn('<p>kept</p>', masked)

    def test_block_regions_are_still_masked(self):
        sources = (
            '{% comment %}\ncomment-marker\n{% endcomment %}',
            '<!--\nhtml-marker\n-->',
            '<script>\nscript-marker\n</script>',
            '<style>\nstyle-marker\n</style>',
        )

        for source in sources:
            with self.subTest(source=source.splitlines()[0]):
                self.assertNotIn('-marker', _mask_ignored_regions(source))

    def test_masking_preserves_line_positions(self):
        masked = _mask_ignored_regions('<!--\nx\n-->\nlast')

        self.assertEqual(masked.splitlines()[3], 'last')


class HeaderOpenerTokenizerTest(SimpleTestCase):
    """The opening tag is single-line, exactly as Django would lex it."""

    def test_opener_does_not_match_across_a_newline(self):
        source = '{% studio_header_actions\n   title="Plans" %}'

        self.assertIsNone(HEADER_OPEN.search(source))
        self.assertIsNone(HEADER_BLOCK.search(
            source + 'body{% endstudio_header_actions %}'
        ))

    def test_single_line_opener_still_matches(self):
        source = '{% studio_header_actions title="Plans" %}'

        self.assertIsNotNone(HEADER_OPEN.search(source))

    def test_block_body_still_spans_lines(self):
        source = (
            '{% studio_header_actions title="Plans" %}\n'
            'first\n'
            'second\n'
            '{% endstudio_header_actions %}'
        )

        match = HEADER_BLOCK.search(source)

        self.assertIsNotNone(match)
        self.assertIn('first', match.group('body'))
        self.assertIn('second', match.group('body'))


class StudioHeaderOwnerRatchetTest(SimpleTestCase):
    def test_every_working_tree_full_page_is_guarded(self):
        pages = _discover_full_page_sources()
        self.assertTrue(pages)
        self.assertEqual(_scan_header_sources(pages), [])

    def test_new_missing_owner_page_fails_without_inventory_edit(self):
        sources = {
            'synthetic/new.html': (
                '{% extends "studio/base.html" %}\n{% block content %}x{% endblock %}'
            ),
        }
        self.assertEqual(
            _scan_header_sources(sources, exceptions={}, ceiling={}),
            [
                'rule=studio-header-owner path=synthetic/new.html line=1 '
                'allowed=1 actual=0 remediation=use-exactly-one-'
                '{% studio_header_actions %}',
            ],
        )

    def test_new_conforming_page_passes_without_inventory_edit(self):
        sources = {
            'synthetic/new.html': (
                '{% extends "studio/base.html" %}\n'
                '{% studio_header_actions title="New" %}'
                '{% endstudio_header_actions %}'
            ),
        }
        self.assertEqual(
            _scan_header_sources(sources, exceptions={}, ceiling={}),
            [],
        )

    def test_opener_split_across_lines_is_not_the_owner(self):
        """Django never lexes a tag whose closer is on a later line.

        Such a page renders `{% studio_header_actions` as literal text and has
        no header owner at all, so the ratchet must report it instead of
        crediting it with a header block it does not have.
        """
        sources = {
            'synthetic/new.html': (
                '{% extends "studio/base.html" %}\n'
                '{% studio_header_actions\n title="New"\n%}'
                '{% endstudio_header_actions %}'
            ),
        }
        diagnostics = _scan_header_sources(sources, exceptions={}, ceiling={})

        self.assertEqual(len(diagnostics), 1)
        self.assertIn('rule=studio-header-owner', diagnostics[0])
        self.assertIn('path=synthetic/new.html', diagnostics[0])

    def test_cleaned_up_exception_is_stale_until_removed(self):
        sources = {
            'worker.html': (
                '{% extends "studio/base.html" %}\n'
                '{% studio_header_actions title="Worker" %}'
                '{% endstudio_header_actions %}'
            ),
        }
        diagnostics = _scan_header_sources(
            sources,
            exceptions={'worker.html': 0},
            ceiling={'worker.html': 0},
        )
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('rule=studio-header-stale-exception', diagnostics[0])
        self.assertIn('path=worker.html line=2 allowed=0 actual=1', diagnostics[0])

    def test_new_or_increased_exception_cannot_raise_the_ceiling(self):
        sources = {
            'worker.html': '{% extends "studio/base.html" %}',
            'synthetic/new.html': '{% extends "studio/base.html" %}',
        }
        diagnostics = _scan_header_sources(
            sources,
            exceptions={'worker.html': 2, 'synthetic/new.html': 0},
            ceiling={'worker.html': 0},
        )
        self.assertTrue(
            any('rule=studio-header-exception-count-ceiling' in row for row in diagnostics),
        )
        self.assertTrue(
            any('rule=studio-header-exception-path-ceiling' in row for row in diagnostics),
        )
        for row in diagnostics:
            self.assertRegex(
                row,
                r'rule=\S+ path=\S+ line=\d+ allowed=\d+ actual=\d+ remediation=',
            )

    def test_matcher_boundaries_and_single_line_tag_rule(self):
        """Lookalike regions are ignored; a real single-line owner is found."""
        sources = {
            'comment-lookalikes.html': (
                '{% extends "studio/base.html" %}\n'
                '{# {% studio_header_actions %} #}\n'
                '{% comment %}{% studio_header_actions %}{% endcomment %}\n'
                '<!-- {% studio_header_actions %} -->\n'
                '<script>"{% studio_header_actions %}"</script>\n'
                '<style>/* {% studio_header_actions %} */</style>'
            ),
            'substring.html': (
                '{% extends "studio/base.html" %}\n'
                '{% studio_header_actions_extra %}'
            ),
            'wrapped-attributes.html': (
                '{% extends "studio/base.html" %}\n'
                '{% studio_header_actions title="Good" subtitle="Also good" %}'
                '{% endstudio_header_actions %}'
            ),
            '_partial.html': '{% studio_header_actions title="Partial" %}',
            'base.html': '<main>{% studio_header_actions title="Base" %}</main>',
        }
        diagnostics = _scan_header_sources(sources, exceptions={}, ceiling={})
        self.assertEqual(len(diagnostics), 2)
        self.assertTrue(any('path=comment-lookalikes.html' in row for row in diagnostics))
        self.assertTrue(any('path=substring.html' in row for row in diagnostics))
        self.assertFalse(any('path=wrapped-attributes.html' in row for row in diagnostics))
        self.assertFalse(any('path=_partial.html' in row for row in diagnostics))
        self.assertFalse(any('path=base.html' in row for row in diagnostics))


@tag('core')
class StudioHeaderConsistencyTest(SimpleTestCase):
    def test_shared_header_blocks_omit_legacy_layout_tokens(self):
        offenders = []
        for relative, source in sorted(_conforming_pages().items()):
            match = HEADER_BLOCK.search(_mask_ignored_regions(source))
            self.assertIsNotNone(match, relative)
            header = match.group(0)
            for token in FORBIDDEN_HEADER_TOKENS:
                if token in header:
                    offenders.append(f'{relative}: {token}')
        self.assertEqual(offenders, [])

    def test_actionless_headers_have_no_action_body_or_test_id(self):
        offenders = []
        for relative in sorted(ACTIONLESS_HEADERS):
            match = HEADER_BLOCK.search(_source(relative))
            self.assertIsNotNone(match, relative)
            if match.group('body').strip():
                offenders.append(f'{relative}: non-empty action body')
            if 'actions_testid' in match.group('opening'):
                offenders.append(f'{relative}: action test ID override')
        self.assertEqual(offenders, [])

    def test_metadata_pages_use_safe_shared_meta_capture(self):
        projects = _source('projects/list.html')
        self.assertIn('{% studio_header_title_meta as projects_header_meta %}', projects)
        self.assertIn('title_meta=projects_header_meta', projects)
        self.assertIn('bg-yellow-500/20', projects)
        self.assertIn('data-testid="projects-pending-meta"', projects)

        for relative in ('campaigns/list.html', 'notifications/list.html', 'workshops/list.html'):
            with self.subTest(relative=relative):
                source = _source(relative)
                self.assertIn('studio_header_title_meta as', source)
                self.assertIn('{% worker_status_inline %}', source)

        sync = _source('sync/dashboard.html')
        self.assertIn('title_meta=sync_header_meta', sync)
        self.assertIn('id="sync-live-indicator"', sync)
        self.assertIn('{% worker_status_inline %}', sync)

    def test_justify_between_remains_only_outside_shared_page_headers(self):
        """Cards, tables, filters and pagers remain valid #1275 non-overlap."""

        offenders = []
        for relative, source in sorted(_conforming_pages().items()):
            match = HEADER_BLOCK.search(source)
            self.assertIsNotNone(match, relative)
            headerless = source[: match.start()] + source[match.end() :]
            if 'justify-between' in match.group(0):
                offenders.append(relative)
            _ = 'justify-between' in headerless
        self.assertEqual(offenders, [])

    def test_settings_has_no_legacy_primary_color_tokens(self):
        studio_source = '\n'.join(
            path.read_text(encoding='utf-8') for path in STUDIO_TEMPLATES.rglob('*.html')
        )
        self.assertNotIn('bg-primary', studio_source)
        self.assertNotIn('text-primary-foreground', studio_source)

    def test_events_keep_visible_and_overflow_action_contract(self):
        source = _source('events/list.html')
        header = HEADER_BLOCK.search(source).group(0)
        self.assertLess(
            header.index('data-testid="event-new-button"'),
            header.index('data-testid="event-past-link"'),
        )
        self.assertLess(
            header.index('data-testid="event-past-link"'),
            header.index('{% studio_overflow_menu %}'),
        )
        self.assertIn("{% url 'studio_event_series_new' %}", header)
        self.assertIn("{% url 'studio_event_duplicates' %}", header)
        self.assertEqual(header.count('bg-accent px-4 py-2'), 1)

    def test_mutating_header_actions_remain_csrf_post_forms(self):
        workshops = HEADER_BLOCK.search(_source('workshops/list.html')).group(0)
        self.assertIn('method="post"', workshops)
        self.assertIn("{% url 'studio_workshop_resync' %}", workshops)
        self.assertIn('{% csrf_token %}', workshops)

        sync = HEADER_BLOCK.search(_source('sync/dashboard.html')).group(0)
        self.assertIn('method="post"', sync)
        self.assertIn("{% url 'studio_sync_all' %}", sync)
        self.assertIn('{% csrf_token %}', sync)

    def test_upload_forms_are_multipart_body_cards_with_preserved_ids(self):
        sync = _source('sync/dashboard.html')
        sync_header_end = HEADER_BLOCK.search(sync).end()
        self.assertGreater(sync.index('data-testid="content-sources-import-card"'), sync_header_end)
        self.assertIn('enctype="multipart/form-data"', sync)
        self.assertIn('data-testid="content-sources-upload"', sync)
        self.assertIn('data-testid="content-sources-download"', sync)
        self.assertIn('disabled', sync)

        settings = PACKAGE_SETTINGS_TEMPLATE.read_text()
        self.assertIn('data-community-settings', settings)
        self.assertIn('community_base_settings_import', settings)
        self.assertIn('community_base_settings_export', settings)
