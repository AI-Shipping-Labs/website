"""Unit tests for the legacy-URL detector (issue #595)."""

from django.test import SimpleTestCase

from content.utils.legacy_urls import (
    LEGACY_URL_PATTERNS,
    LEGACY_URL_REPLACEMENTS,
    detect_legacy_urls,
    detect_relative_links,
)


class DetectLegacyUrlsTest(SimpleTestCase):
    """Cover every branch of :func:`detect_legacy_urls`."""

    def test_returns_empty_on_empty_html(self):
        errors = []
        self.assertEqual(detect_legacy_urls('', 'blog/foo.md', errors), [])
        self.assertEqual(detect_legacy_urls(None, 'blog/foo.md', errors), [])
        self.assertEqual(errors, [])

    def test_clean_html_records_no_warnings(self):
        html = (
            '<p>Read the <a href="/events/coding-agent-skills-commands">'
            'workshop recap</a>.</p>'
        )
        errors = []
        found = detect_legacy_urls(html, 'blog/foo.md', errors)
        self.assertEqual(found, [])
        self.assertEqual(errors, [])

    def test_legacy_event_recordings_url_records_warning(self):
        html = (
            '<p>See <a href="/event-recordings/coding-agent-skills-commands">'
            'the workshop</a>.</p>'
        )
        errors = []
        found = detect_legacy_urls(
            html, 'blog/home-oai-folder-and-openai-skills.md', errors,
        )
        self.assertEqual(
            found, ['/event-recordings/coding-agent-skills-commands'],
        )
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(
            record['file'], 'blog/home-oai-folder-and-openai-skills.md',
        )
        # Message must mention both the source path and the offending URL.
        self.assertIn(
            'blog/home-oai-folder-and-openai-skills.md', record['error'],
        )
        self.assertIn(
            '/event-recordings/coding-agent-skills-commands', record['error'],
        )
        # And it must suggest the replacement so authors see the fix.
        self.assertIn(
            '/events/coding-agent-skills-commands', record['error'],
        )

    def test_multiple_legacy_links_each_emit_a_warning(self):
        html = (
            '<a href="/event-recordings/foo">a</a>'
            '<a href="/event-recordings/bar">b</a>'
        )
        errors = []
        found = detect_legacy_urls(html, 'blog/x.md', errors)
        self.assertEqual(found, [
            '/event-recordings/foo',
            '/event-recordings/bar',
        ])
        self.assertEqual(len(errors), 2)

    def test_single_quoted_href_is_detected(self):
        html = "<a href='/event-recordings/foo'>x</a>"
        errors = []
        found = detect_legacy_urls(html, 'blog/x.md', errors)
        self.assertEqual(found, ['/event-recordings/foo'])
        self.assertEqual(len(errors), 1)

    def test_href_with_extra_attrs_is_detected(self):
        html = (
            '<a class="external" href="/event-recordings/foo" '
            'rel="noopener">x</a>'
        )
        errors = []
        found = detect_legacy_urls(html, 'blog/x.md', errors)
        self.assertEqual(found, ['/event-recordings/foo'])
        self.assertEqual(len(errors), 1)

    def test_substring_match_in_other_path_does_not_fire(self):
        # An /events/ link that happens to contain the substring
        # "event-recordings" inside the slug must NOT match — we only
        # want path-prefix hits, which is why the regex anchors on the
        # quote+slash before the prefix.
        html = '<a href="/events/event-recordings-overview">x</a>'
        errors = []
        found = detect_legacy_urls(html, 'blog/x.md', errors)
        self.assertEqual(found, [])
        self.assertEqual(errors, [])

    def test_external_url_with_legacy_path_does_not_fire(self):
        # Only root-relative links count. An external URL that happens
        # to contain /event-recordings/ is on someone else's site.
        html = (
            '<a href="https://other.example.com/event-recordings/foo">x</a>'
        )
        errors = []
        found = detect_legacy_urls(html, 'blog/x.md', errors)
        self.assertEqual(found, [])
        self.assertEqual(errors, [])

    def test_sync_errors_none_disables_side_effects(self):
        # Passing sync_errors=None must still return the list of finds,
        # so callers can use the helper for read-only inspection.
        html = '<a href="/event-recordings/foo">x</a>'
        found = detect_legacy_urls(html, 'blog/x.md', None)
        self.assertEqual(found, ['/event-recordings/foo'])

    def test_pattern_constants_are_consistent(self):
        # Every pattern must start with "/" and end with "/" so the
        # regex matches whole path segments. Replacements (when present)
        # must follow the same shape.
        for prefix in LEGACY_URL_PATTERNS:
            self.assertTrue(prefix.startswith('/'))
            self.assertTrue(prefix.endswith('/'))
        for prefix, replacement in LEGACY_URL_REPLACEMENTS.items():
            self.assertIn(prefix, LEGACY_URL_PATTERNS)
            self.assertTrue(replacement.startswith('/'))
            self.assertTrue(replacement.endswith('/'))


class DetectRelativeLinksTest(SimpleTestCase):
    """Cover :func:`detect_relative_links` (issue #1342)."""

    def test_returns_empty_on_empty_html(self):
        errors = []
        self.assertEqual(detect_relative_links('', 'wk/README.md', errors), [])
        self.assertEqual(detect_relative_links(None, 'wk/README.md', errors), [])
        self.assertEqual(errors, [])

    def test_parent_relative_link_records_one_warning(self):
        # The exact shape from the reported bug: a link that climbs out of
        # the workshop folder into a sibling date-slug directory.
        href = '../../06/2026-06-29-selecting-a-portfolio-project/'
        html = f'<p>See <a href="{href}">selecting a portfolio project</a>.</p>'
        errors = []
        found = detect_relative_links(html, 'wk/README.md', errors)
        self.assertEqual(found, [href])
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(record['file'], 'wk/README.md')
        # Message names the source file and echoes the offending href.
        self.assertIn('wk/README.md', record['error'])
        self.assertIn(href, record['error'])
        # And it must hint the fix: a canonical /... site URL.
        self.assertIn('/... site URL', record['error'])

    def test_current_dir_relative_link_is_detected(self):
        html = '<a href="./05-tailor-to-domain/">next page</a>'
        errors = []
        found = detect_relative_links(html, 'wk/README.md', errors)
        self.assertEqual(found, ['./05-tailor-to-domain/'])
        self.assertEqual(len(errors), 1)

    def test_multiple_relative_links_each_emit_a_warning(self):
        html = (
            '<a href="../a/">a</a>'
            '<a href="./b/">b</a>'
        )
        errors = []
        found = detect_relative_links(html, 'wk/x.md', errors)
        self.assertEqual(found, ['../a/', './b/'])
        self.assertEqual(len(errors), 2)

    def test_single_quoted_and_extra_attrs_detected(self):
        html = (
            "<a class='xref' href='../sibling/' rel='noopener'>x</a>"
        )
        errors = []
        found = detect_relative_links(html, 'wk/x.md', errors)
        self.assertEqual(found, ['../sibling/'])
        self.assertEqual(len(errors), 1)

    def test_no_false_positives_on_valid_link_shapes(self):
        # Root-relative site routes, external URLs, in-page anchors,
        # mailto and tel links must NEVER be flagged.
        clean_hrefs = [
            '/workshops/selecting-a-portfolio-project',
            '/blog/some-article',
            'https://example.com/event-recordings/foo',
            'http://example.com/x',
            '#section-two',
            'mailto:hi@aishippinglabs.com',
            'tel:+15551234567',
        ]
        for href in clean_hrefs:
            with self.subTest(href=href):
                html = f'<a href="{href}">x</a>'
                errors = []
                found = detect_relative_links(html, 'wk/x.md', errors)
                self.assertEqual(found, [])
                self.assertEqual(errors, [])

    def test_sync_errors_none_disables_side_effects(self):
        html = '<a href="../foo/">x</a>'
        found = detect_relative_links(html, 'wk/x.md', None)
        self.assertEqual(found, ['../foo/'])

    def test_html_is_not_modified(self):
        # The guard must never rewrite the link — it is advisory only.
        html = '<a href="../../06/foo/">x</a>'
        errors = []
        detect_relative_links(html, 'wk/x.md', errors)
        self.assertIn('href="../../06/foo/"', html)

    def test_skips_link_already_flagged_by_prior_warning(self):
        # Issue #1342 Part B: when a prior sync step (the workshop
        # cross-workshop rewriter) has already quoted the target folder in a
        # warning, the detector must NOT add a second warning for the same
        # single-level ``../<folder>/`` link.
        errors = [{
            'file': 'wk/01-overview.md',
            'error': (
                'Cross-workshop link "2099-12-31-deleted-workshop" in '
                'wk/01-overview.md: target folder '
                '"2099-12-31-deleted-workshop" not found in synced workshops.'
            ),
        }]
        html = '<a href="../2099-12-31-deleted-workshop/">gone</a>'
        found = detect_relative_links(html, 'wk/01-overview.md', errors)
        # The href is still reported as "found"...
        self.assertEqual(found, ['../2099-12-31-deleted-workshop/'])
        # ...but no NEW warning was appended (only the pre-existing one stays).
        self.assertEqual(len(errors), 1)
        self.assertIn('Cross-workshop link', errors[0]['error'])

    def test_deeper_link_still_warns_when_prior_warning_is_unrelated(self):
        # A deeper ``../../<month>/<workshop>/`` link the rewriter never
        # processes must still warn even though an unrelated prior warning
        # exists — its month-folder token ("06") is not quoted upstream.
        errors = [{
            'file': 'wk/01-overview.md',
            'error': (
                'Cross-workshop link "2099-12-31-deleted-workshop" in '
                'wk/01-overview.md: target folder '
                '"2099-12-31-deleted-workshop" not found in synced workshops.'
            ),
        }]
        href = '../../06/2026-06-29-tailor-cv/'
        html = f'<a href="{href}">x</a>'
        found = detect_relative_links(html, 'wk/01-overview.md', errors)
        self.assertEqual(found, [href])
        self.assertEqual(len(errors), 2)
        self.assertIn('Relative content-repo link', errors[1]['error'])
        self.assertIn(href, errors[1]['error'])
