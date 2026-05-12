"""Unit tests for the legacy-URL detector (issue #595)."""

from django.test import SimpleTestCase

from content.utils.legacy_urls import (
    LEGACY_URL_PATTERNS,
    LEGACY_URL_REPLACEMENTS,
    detect_legacy_urls,
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
