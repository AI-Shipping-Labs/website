"""Tests for the markdown link rewriter (issue #226).

Authors write internal links between course units using natural relative
markdown form ([Setup](02-setup.md)). The platform serves units at URLs
without `.md` and with the numeric prefix stripped, so without rewriting
every internal link 404s.

Covers:
- Sibling `*.md` link resolves
- Cross-module same-course link resolves
- README.md sibling link resolves to the module overview URL (issue #222)
- External http(s):// links untouched
- Anchor-only links untouched
- Unresolvable .md link is left intact and a warning is emitted
- Image links (`![alt](path.png)`) are not touched
- Anchor fragments are preserved
"""

from django.test import TestCase

from content.utils.md_links import rewrite_md_links, rewrite_workshop_md_links

COURSE_LOOKUP = {
    'fundamentals': {
        # README.md is the module overview (issue #222), not a unit.
        # The sync registers it under the sentinel slug
        # ``__module_overview__`` so the rewriter can map it to the bare
        # module URL ``/<course>/<module>/``.
        'README.md': '__module_overview__',
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

    def test_readme_sibling_resolves_to_module_overview_url(self):
        """README.md links resolve to the bare module URL (issue #222).

        The README is now the module's overview page rather than a sibling
        ``/readme`` unit, so the rewriter must produce
        ``/courses/<course>/<module>`` (no trailing ``readme``, no trailing
        slash — the project uses ``RemoveTrailingSlashMiddleware``).
        """
        body = 'Back to [the overview](README.md).'
        result = rewrite_md_links(
            body,
            course_slug='python',
            module_slug='fundamentals',
            unit_lookup=COURSE_LOOKUP,
        )
        self.assertIn(
            '[the overview](/courses/python/fundamentals)',
            result,
        )
        self.assertNotIn('readme)', result)

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


WORKSHOP_LOOKUP = {
    '01-overview.md': {
        'slug': 'overview',
        'title': 'Welcome and overview',
        'url': '/workshops/end-to-end-agent-deployment/tutorial/overview',
    },
    '02-starting-notebook.md': {
        'slug': 'starting-notebook',
        'title': 'Part 1: The starting notebook',
        'url': '/workshops/end-to-end-agent-deployment/tutorial/starting-notebook',
    },
    '10-qa.md': {
        'slug': 'qa',
        'title': 'Q&A: side discussions',
        'url': '/workshops/end-to-end-agent-deployment/tutorial/qa',
    },
}


class RewriteWorkshopMdLinksTest(TestCase):
    """Unit tests for resolution rules in rewrite_workshop_md_links (issue #301)."""

    workshop_slug = 'end-to-end-agent-deployment'

    def _rewrite(self, body, **kwargs):
        kwargs.setdefault('workshop_slug', self.workshop_slug)
        kwargs.setdefault('page_lookup', WORKSHOP_LOOKUP)
        return rewrite_workshop_md_links(body, **kwargs)

    def test_text_equal_to_filename_swaps_to_target_title(self):
        """[10-qa.md](10-qa.md) -> [<title>](<url>) (text replaced)."""
        body = 'See [10-qa.md](10-qa.md) for why and how.'
        result = self._rewrite(body)
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )
        # The bare filename must NOT appear as link text or as a URL.
        self.assertNotIn('](10-qa.md)', result)
        self.assertNotIn('[10-qa.md]', result)

    def test_custom_label_preserved_only_url_rewritten(self):
        """[the Q&A page](10-qa.md) -> [the Q&A page](<url>): text preserved."""
        body = 'For details see [the Q&A page](10-qa.md).'
        result = self._rewrite(body)
        self.assertIn(
            '[the Q&A page]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_whitespace_padded_filename_text_still_swaps_title(self):
        """[ 10-qa.md ](10-qa.md): label.strip() equals filename, swap fires."""
        body = 'Check [ 10-qa.md ](10-qa.md) again.'
        result = self._rewrite(body)
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_anchor_fragment_preserved_with_title_swap(self):
        """[10-qa.md](10-qa.md#tmux): fragment kept, label becomes title."""
        body = 'See [10-qa.md](10-qa.md#tmux) for the tmux notes.'
        result = self._rewrite(body)
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            result,
        )

    def test_anchor_fragment_preserved_with_custom_label(self):
        """Custom label, anchor preserved on the URL."""
        body = 'For tmux details see [the Q&A page](10-qa.md#tmux).'
        result = self._rewrite(body)
        self.assertIn(
            '[the Q&A page]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            result,
        )

    def test_case_insensitive_filename_lookup(self):
        """[10-QA.md](10-qa.md): filename lookup falls back to lower-case."""
        body = '[10-QA.md](10-qa.md)'
        result = self._rewrite(body)
        # URL still resolves via case-insensitive fallback ...
        self.assertIn(
            '/workshops/end-to-end-agent-deployment/tutorial/qa',
            result,
        )
        # ... and the title swap still fires because the label, lower-cased
        # and stripped, equals the canonical filename.
        self.assertIn('[Q&A: side discussions]', result)

    def test_unresolvable_md_link_left_intact_with_warning(self):
        """[gone](99-deleted.md): link kept, warning appended to sync_errors."""
        body = 'Old: [gone](99-deleted.md).'
        errors = []
        result = self._rewrite(
            body,
            source_path='2026-04-21-end-to-end-agent-deployment/01-overview.md',
            sync_errors=errors,
        )
        self.assertIn('[gone](99-deleted.md)', result)
        self.assertEqual(len(errors), 1)
        self.assertIn('99-deleted.md', errors[0]['error'])
        self.assertIn(self.workshop_slug, errors[0]['error'])
        self.assertEqual(
            errors[0]['file'],
            '2026-04-21-end-to-end-agent-deployment/01-overview.md',
        )

    def test_cross_workshop_link_left_intact_with_warning(self):
        """[over there](../other-workshop/01-foo.md): out-of-tree, warned."""
        body = 'Compare [over there](../other-workshop/01-foo.md).'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertIn('[over there](../other-workshop/01-foo.md)', result)
        self.assertEqual(len(errors), 1)
        self.assertIn('Cross-workshop', errors[0]['error'])

    def test_external_link_untouched_no_warning(self):
        body = 'Read the [docs](https://example.com/x.md).'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_anchor_only_link_untouched_no_warning(self):
        body = 'Jump to [setup section](#setup).'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_non_md_link_untouched_no_warning(self):
        """Links to .png / .py / .ipynb are out of scope — left alone."""
        body = (
            'Get [the script](script.py) and [the picture](image.png) '
            'and [the notebook](data.ipynb).'
        )
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_image_syntax_with_md_target_is_not_rewritten(self):
        """``![alt](10-qa.md)`` is image syntax — the link regex excludes it."""
        body = '![alt](10-qa.md) and a real link [10-qa.md](10-qa.md).'
        result = self._rewrite(body)
        # Image is preserved verbatim.
        self.assertIn('![alt](10-qa.md)', result)
        # The non-image link is rewritten.
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_leading_dot_slash_resolves_as_sibling(self):
        """``./10-qa.md`` is an explicit sibling reference — same as ``10-qa.md``."""
        body = 'See [10-qa.md](./10-qa.md) again.'
        result = self._rewrite(body)
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_subfolder_link_left_intact_with_warning(self):
        """Workshops are flat — a/path/to.md cannot be a sibling."""
        body = '[nested](sub/foo.md)'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertIn('[nested](sub/foo.md)', result)
        self.assertEqual(len(errors), 1)

    def test_empty_body_returns_empty(self):
        self.assertEqual(self._rewrite(''), '')

    def test_missing_workshop_slug_skips_rewrite(self):
        body = '[10-qa.md](10-qa.md)'
        result = rewrite_workshop_md_links(
            body, workshop_slug='', page_lookup=WORKSHOP_LOOKUP,
        )
        self.assertEqual(body, result)

    def test_multiple_links_rewritten_in_one_body(self):
        body = (
            'The agentic-RAG explanation is in '
            '[02-starting-notebook.md](02-starting-notebook.md). '
            'See [10-qa.md](10-qa.md) for why and how. '
            'For tmux details see [the Q&A page](10-qa.md#tmux).'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[Part 1: The starting notebook]'
            '(/workshops/end-to-end-agent-deployment/tutorial/starting-notebook)',
            result,
        )
        self.assertIn(
            '[Q&A: side discussions]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )
        self.assertIn(
            '[the Q&A page]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            result,
        )
