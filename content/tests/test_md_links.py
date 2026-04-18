"""Tests for the markdown link rewriter (issue #226).

Authors write internal links between course units using natural relative
markdown form ([Setup](02-setup.md)). The platform serves units at URLs
without `.md` and with the numeric prefix stripped, so without rewriting
every internal link 404s.

Covers:
- Sibling `*.md` link resolves
- Cross-module same-course link resolves
- README.md sibling link resolves to the README-as-unit
- External http(s):// links untouched
- Anchor-only links untouched
- Unresolvable .md link is left intact and a warning is emitted
- Image links (`![alt](path.png)`) are not touched
- Anchor fragments are preserved
"""

from django.test import TestCase

from content.utils.md_links import rewrite_md_links

COURSE_LOOKUP = {
    'fundamentals': {
        'README.md': 'readme',
        '01-intro.md': 'intro',
        '02-setup.md': 'setup',
    },
    'advanced': {
        '01-pipelines.md': 'pipelines',
        '02-deploy.md': 'deploy',
    },
}


class RewriteMdLinksTest(TestCase):
    """Unit tests for resolution rules in rewrite_md_links."""

    def test_sibling_md_link_resolves_to_platform_url(self):
        body = 'Continue with [Setup](02-setup.md).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn('[Setup](/courses/python/fundamentals/setup)', result)
        self.assertNotIn('02-setup.md', result)

    def test_cross_module_same_course_link_resolves(self):
        body = 'See [Deploy](../advanced/02-deploy.md) later.'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn(
            '[Deploy](/courses/python/advanced/deploy)',
            result,
        )

    def test_readme_sibling_resolves_to_module_readme_unit(self):
        body = 'Back to [the overview](README.md).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn(
            '[the overview](/courses/python/fundamentals/readme)',
            result,
        )

    def test_external_http_link_untouched(self):
        body = 'Read the [docs](https://example.com/page).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertEqual(body, result)

    def test_external_http_md_link_untouched(self):
        """An http link to a .md file (e.g. on GitHub) must not be rewritten."""
        body = 'See [the source](https://github.com/foo/bar/blob/main/file.md).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertEqual(body, result)

    def test_anchor_only_link_untouched(self):
        body = 'Jump to [section](#installation).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertEqual(body, result)

    def test_image_link_not_rewritten(self):
        """Image syntax `![alt](path.md)` is not a thing in practice but the
        rewriter must never touch images."""
        body = '![diagram](diagram.png) and ![note](02-setup.md)'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        # The image syntax is preserved verbatim — neither path is treated
        # as a markdown link.
        self.assertIn('![diagram](diagram.png)', result)
        self.assertIn('![note](02-setup.md)', result)

    def test_unresolvable_md_link_left_intact_with_warning(self):
        body = 'Coming soon: [Future](99-not-yet.md).'
        errors = []
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
            source_path='python/fundamentals/01-intro.md',
            sync_errors=errors,
        )
        # Link text and original target are unchanged.
        self.assertIn('[Future](99-not-yet.md)', result)
        # Warning surfaced into the SyncLog errors list.
        self.assertEqual(len(errors), 1)
        self.assertIn('99-not-yet.md', errors[0]['error'])
        self.assertEqual(errors[0]['file'], 'python/fundamentals/01-intro.md')

    def test_unresolvable_cross_module_link_warns(self):
        body = '[Missing](../missing-module/01-foo.md)'
        errors = []
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
            source_path='python/fundamentals/01-intro.md',
            sync_errors=errors,
        )
        self.assertIn('[Missing](../missing-module/01-foo.md)', result)
        self.assertEqual(len(errors), 1)

    def test_anchor_fragment_preserved_on_rewrite(self):
        body = 'See [Setup, virtualenv section](02-setup.md#virtualenv).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn(
            '[Setup, virtualenv section]'
            '(/courses/python/fundamentals/setup#virtualenv)',
            result,
        )

    def test_cross_course_link_left_alone_with_warning(self):
        """Two-level `..` escapes the course; should be left intact."""
        body = '[Other course](../../other-course/module/01-foo.md)'
        errors = []
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
            sync_errors=errors,
        )
        self.assertIn('[Other course](../../other-course/module/01-foo.md)', result)
        self.assertEqual(len(errors), 1)

    def test_non_md_link_untouched(self):
        """Links to .py, .pdf, etc must be left alone."""
        body = 'Download [notebook](data/notebook.ipynb) and [code](script.py).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertEqual(body, result)

    def test_empty_body_returns_empty(self):
        self.assertEqual(rewrite_md_links('', 'c', 'm', COURSE_LOOKUP), '')

    def test_missing_course_slug_skips_rewrite(self):
        body = 'Continue with [Setup](02-setup.md).'
        result = rewrite_md_links(
            body,
            course_slug='',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertEqual(body, result)

    def test_module_dir_with_numeric_prefix_resolves(self):
        """Authors often write the on-disk dir name `../03-advanced/...`
        instead of the slug `../advanced/...`. Both must resolve."""
        body = 'See [Deploy](../03-advanced/02-deploy.md).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn(
            '[Deploy](/courses/python/advanced/deploy)',
            result,
        )

    def test_multiple_links_in_one_body(self):
        body = (
            'First [Intro](01-intro.md), then [Setup](02-setup.md), '
            'then [Deploy](../advanced/02-deploy.md), and an external '
            '[link](https://example.com).'
        )
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn('/courses/python/fundamentals/intro', result)
        self.assertIn('/courses/python/fundamentals/setup', result)
        self.assertIn('/courses/python/advanced/deploy', result)
        self.assertIn('https://example.com', result)
