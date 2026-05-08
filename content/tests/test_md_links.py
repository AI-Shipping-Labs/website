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

from content.utils.md_links import (
    rewrite_cross_workshop_md_links,
    rewrite_md_links,
    rewrite_workshop_md_links,
)

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


# Lookup that includes the README.md virtual entry produced by
# ``_build_workshop_page_lookup`` post issue #304. README.md routes to the
# workshop landing URL (no /tutorial/ prefix) and uses ``slug=''`` plus the
# workshop title so the title-substitution rule surfaces a friendly label.
WORKSHOP_LOOKUP_WITH_README = {
    '01-overview.md': {
        'slug': 'overview',
        'title': 'Welcome and overview',
        'url': '/workshops/end-to-end-agent-deployment/tutorial/overview',
    },
    '10-qa.md': {
        'slug': 'qa',
        'title': 'Q&A: side discussions',
        'url': '/workshops/end-to-end-agent-deployment/tutorial/qa',
    },
    'README.md': {
        'slug': '',
        'title': 'Production Agents',
        'url': '/workshops/end-to-end-agent-deployment',
    },
}


class RewriteWorkshopMdLinksReadmeVirtualEntryTest(TestCase):
    """Issue #304: README.md (and copy_file) virtual entries route to the
    workshop landing URL with the workshop title as the substituted label."""

    workshop_slug = 'end-to-end-agent-deployment'

    def _rewrite(self, body, lookup=None, **kwargs):
        kwargs.setdefault('workshop_slug', self.workshop_slug)
        kwargs.setdefault(
            'page_lookup', lookup or WORKSHOP_LOOKUP_WITH_README,
        )
        return rewrite_workshop_md_links(body, **kwargs)

    def test_readme_md_link_rewrites_to_workshop_landing_with_title(self):
        body = 'See [README.md](README.md).'
        result = self._rewrite(body)
        # No /tutorial/ in the URL — README routes to the bare landing.
        self.assertIn(
            '[Production Agents](/workshops/end-to-end-agent-deployment)',
            result,
        )
        self.assertNotIn('](README.md)', result)
        self.assertNotIn('/tutorial/', result)

    def test_readme_md_link_with_custom_label_preserves_label(self):
        body = 'See [the intro](README.md).'
        result = self._rewrite(body)
        self.assertIn(
            '[the intro](/workshops/end-to-end-agent-deployment)',
            result,
        )

    def test_readme_md_link_anchor_preserved(self):
        body = 'See [link](README.md#setup).'
        result = self._rewrite(body)
        self.assertIn(
            '[link](/workshops/end-to-end-agent-deployment#setup)',
            result,
        )

    def test_readme_md_link_emits_no_warning(self):
        # The whole point of #304: README links no longer surface as
        # broken-link warnings on SyncLog.
        body = 'See [README.md](README.md).'
        errors = []
        self._rewrite(body, sync_errors=errors)
        self.assertEqual(errors, [])

    def test_readme_md_case_insensitive_resolves_to_landing(self):
        # readme.md, Readme.md, README.MD all map to the README.md entry.
        for variant in ('readme.md', 'Readme.md', 'README.MD'):
            body = f'See [x]({variant}).'
            result = self._rewrite(body)
            self.assertIn(
                '[x](/workshops/end-to-end-agent-deployment)',
                result,
                f'Failed for variant {variant!r}: {result}',
            )

    def test_copy_file_virtual_entry_routes_to_landing(self):
        # When copy_file: 01-intro.md, the rewriter sees a virtual entry for
        # 01-intro.md that points at the landing URL (overriding the
        # tutorial-page entry the same filename would otherwise produce).
        lookup = {
            # 01-intro.md is overridden to point at the landing — this
            # mirrors what _build_workshop_page_lookup does when
            # copy_file is set.
            '01-intro.md': {
                'slug': '',
                'title': 'Production Agents',
                'url': '/workshops/end-to-end-agent-deployment',
            },
            '02-next.md': {
                'slug': 'next',
                'title': 'Next',
                'url': '/workshops/end-to-end-agent-deployment/tutorial/next',
            },
        }
        body = 'See [01-intro.md](01-intro.md).'
        result = self._rewrite(body, lookup=lookup)
        self.assertIn(
            '[Production Agents](/workshops/end-to-end-agent-deployment)',
            result,
        )
        self.assertNotIn('/tutorial/intro', result)


# Sync-wide cross-workshop lookup keyed by on-disk dated-slug folder name
# (issue #526). One source workshop (lambda-agent-deployment) links to
# one target workshop (end-to-end-agent-deployment).
CROSS_WORKSHOP_LOOKUP = {
    '2026-04-21-end-to-end-agent-deployment': {
        'slug': 'end-to-end-agent-deployment',
        'title': 'End-to-End Agent Deployment',
        'content_id': 'd754ae83-3f43-4c35-9737-f89205de5e3c',
        'url': '/workshops/end-to-end-agent-deployment',
        'pages': {
            '01-overview.md': {'slug': 'overview', 'title': 'Overview'},
            '10-qa.md': {'slug': 'qa', 'title': 'Q&A'},
        },
    },
    '2026-05-05-lambda-agent-deployment': {
        'slug': 'lambda-agent-deployment',
        'title': 'Deploying an Agent to AWS Lambda',
        'content_id': '3fe4f80c-dba1-4d20-a4dc-bbfc014bbf16',
        'url': '/workshops/lambda-agent-deployment',
        'pages': {
            '01-overview.md': {
                'slug': 'overview', 'title': 'Overview and setup',
            },
        },
    },
}

WORKSHOPS_REPO = 'AI-Shipping-Labs/workshops'


class RewriteCrossWorkshopMdLinksTest(TestCase):
    """Unit tests for ``rewrite_cross_workshop_md_links`` (issue #526).

    Authors link ACROSS workshops in two shapes today: ``..``-relative
    paths and absolute GitHub URLs into the workshops repo. Both must be
    rewritten to native ``/workshops/<slug>`` URLs so the rendered HTML
    doesn't 404.
    """

    source_folder = '2026-05-05-lambda-agent-deployment'
    source_path = (
        '2026/2026-05-05-lambda-agent-deployment/01-overview.md'
    )

    def _rewrite(self, body, **kwargs):
        kwargs.setdefault(
            'cross_workshop_lookup', CROSS_WORKSHOP_LOOKUP,
        )
        kwargs.setdefault('workshops_repo_name', WORKSHOPS_REPO)
        kwargs.setdefault('source_workshop_folder', self.source_folder)
        kwargs.setdefault('source_path', self.source_path)
        return rewrite_cross_workshop_md_links(body, **kwargs)

    def test_relative_link_with_trailing_slash_resolves_to_landing(self):
        body = (
            'A follow-up to '
            '[the previous workshop](../2026-04-21-end-to-end-agent-deployment/).'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[the previous workshop](/workshops/end-to-end-agent-deployment)',
            result,
        )
        self.assertNotIn('../2026-04-21-end-to-end-agent-deployment', result)

    def test_relative_link_no_trailing_slash_resolves_to_landing(self):
        body = '[label](../2026-04-21-end-to-end-agent-deployment)'
        result = self._rewrite(body)
        self.assertIn(
            '[label](/workshops/end-to-end-agent-deployment)',
            result,
        )

    def test_relative_link_with_md_subpage_resolves_to_tutorial(self):
        body = '[Q&A](../2026-04-21-end-to-end-agent-deployment/10-qa.md)'
        result = self._rewrite(body)
        self.assertIn(
            '[Q&A](/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_relative_md_subpage_preserves_anchor_fragment(self):
        body = (
            '[link](../2026-04-21-end-to-end-agent-deployment/10-qa.md#tmux)'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[link]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            result,
        )

    def test_relative_landing_preserves_anchor_fragment(self):
        body = (
            '[link]'
            '(../2026-04-21-end-to-end-agent-deployment/#prerequisites)'
        )
        result = self._rewrite(body)
        # Trailing-slash + fragment: regex sees folder + sub='' + frag.
        self.assertIn(
            '[link](/workshops/end-to-end-agent-deployment#prerequisites)',
            result,
        )

    def test_github_tree_url_resolves_to_landing(self):
        body = (
            '[label](https://github.com/AI-Shipping-Labs/workshops/tree/'
            'main/2026/2026-04-21-end-to-end-agent-deployment)'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[label](/workshops/end-to-end-agent-deployment)',
            result,
        )
        self.assertNotIn('github.com', result)

    def test_github_tree_url_trailing_slash_resolves_to_landing(self):
        body = (
            '[label](https://github.com/AI-Shipping-Labs/workshops/tree/'
            'main/2026/2026-04-21-end-to-end-agent-deployment/)'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[label](/workshops/end-to-end-agent-deployment)',
            result,
        )

    def test_github_blob_md_url_resolves_to_tutorial(self):
        body = (
            '[label](https://github.com/AI-Shipping-Labs/workshops/blob/'
            'main/2026/2026-04-21-end-to-end-agent-deployment/10-qa.md)'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[label](/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_github_blob_url_with_anchor_preserves_fragment(self):
        body = (
            '[link](https://github.com/AI-Shipping-Labs/workshops/blob/'
            'main/2026/2026-04-21-end-to-end-agent-deployment/10-qa.md#tmux)'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[link]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            result,
        )

    def test_unknown_folder_relative_left_intact_with_warning(self):
        body = '[gone](../2099-12-31-deleted-workshop/)'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertIn('[gone](../2099-12-31-deleted-workshop/)', result)
        self.assertEqual(len(errors), 1)
        self.assertIn('2099-12-31-deleted-workshop', errors[0]['error'])
        self.assertIn('not found', errors[0]['error'])
        self.assertEqual(errors[0]['file'], self.source_path)

    def test_unknown_folder_github_url_left_intact_with_warning(self):
        body = (
            '[gone](https://github.com/AI-Shipping-Labs/workshops/tree/'
            'main/2099/2099-12-31-deleted-workshop)'
        )
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertIn('2099-12-31-deleted-workshop', result)
        self.assertIn('github.com', result)
        self.assertEqual(len(errors), 1)
        self.assertIn('2099-12-31-deleted-workshop', errors[0]['error'])

    def test_missing_subpage_left_intact_with_warning(self):
        body = (
            '[bad]'
            '(../2026-04-21-end-to-end-agent-deployment/99-missing.md)'
        )
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertIn('99-missing.md', result)
        self.assertEqual(len(errors), 1)
        self.assertIn('99-missing.md', errors[0]['error'])
        self.assertIn('2026-04-21-end-to-end-agent-deployment',
                      errors[0]['error'])

    def test_non_md_subpath_inside_existing_workshop_untouched_no_warning(self):
        """``deploy.sh`` etc. point at code, not pages — leave alone, no warn."""
        body = (
            '[script]'
            '(../2026-04-21-end-to-end-agent-deployment/deploy.sh)'
        )
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_subdirectory_inside_existing_workshop_untouched_no_warning(self):
        body = (
            '[scripts]'
            '(../2026-04-21-end-to-end-agent-deployment/deploy/scripts)'
        )
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_github_url_to_different_repo_untouched_no_warning(self):
        body = '[link](https://github.com/other-org/repo/tree/main/foo)'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_github_url_to_workshops_repo_subdir_untouched_no_warning(self):
        """Code inside an existing workshop folder remains a GitHub URL."""
        body = (
            '[deploy scripts]'
            '(https://github.com/AI-Shipping-Labs/workshops/tree/main/'
            '2026/2026-05-05-lambda-agent-deployment/deploy/scripts)'
        )
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_image_syntax_is_not_rewritten(self):
        body = (
            '![diagram]'
            '(../2026-04-21-end-to-end-agent-deployment/diagram.png)'
        )
        result = self._rewrite(body)
        # Image syntax is preserved verbatim — `(?<!\!)` excludes it.
        self.assertEqual(body, result)

    def test_image_syntax_with_md_target_is_not_rewritten(self):
        body = (
            '![alt]'
            '(../2026-04-21-end-to-end-agent-deployment/10-qa.md) '
            'and a real link '
            '[Q&A](../2026-04-21-end-to-end-agent-deployment/10-qa.md).'
        )
        result = self._rewrite(body)
        # Image left alone.
        self.assertIn(
            '![alt](../2026-04-21-end-to-end-agent-deployment/10-qa.md)',
            result,
        )
        # Non-image link rewritten.
        self.assertIn(
            '[Q&A](/workshops/end-to-end-agent-deployment/tutorial/qa)',
            result,
        )

    def test_two_levels_up_left_intact(self):
        """``../../foo/bar.md`` is out-of-tree — must remain untouched."""
        body = '[far](../../foo/bar.md)'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        # No warning either — out-of-tree is not the cross-workshop pass's
        # concern.
        self.assertEqual(errors, [])

    def test_repo_name_is_configurable(self):
        """A different repo name in the lookup matches that host, not the default."""
        body = (
            '[link](https://github.com/Other/workshops-repo/tree/'
            'main/2026/2026-04-21-end-to-end-agent-deployment)'
        )
        # Rewriter only matches the configured repo: with a different repo
        # name the URL is left alone.
        result = rewrite_cross_workshop_md_links(
            body,
            cross_workshop_lookup=CROSS_WORKSHOP_LOOKUP,
            workshops_repo_name='AI-Shipping-Labs/workshops',
        )
        self.assertEqual(body, result)
        # And with the matching repo name it rewrites.
        result_match = rewrite_cross_workshop_md_links(
            body,
            cross_workshop_lookup=CROSS_WORKSHOP_LOOKUP,
            workshops_repo_name='Other/workshops-repo',
        )
        self.assertIn(
            '[link](/workshops/end-to-end-agent-deployment)',
            result_match,
        )

    def test_branch_name_is_permissive(self):
        """Authors don't always pin to ``main`` — accept any branch name."""
        body = (
            '[link](https://github.com/AI-Shipping-Labs/workshops/tree/'
            'develop/2026/2026-04-21-end-to-end-agent-deployment)'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[link](/workshops/end-to-end-agent-deployment)',
            result,
        )

    def test_empty_body_returns_empty(self):
        self.assertEqual(self._rewrite(''), '')

    def test_no_links_in_body_returned_unchanged(self):
        body = 'Plain prose with no links.'
        self.assertEqual(self._rewrite(body), body)

    def test_anchor_only_link_left_intact_no_warning(self):
        body = 'Jump to [section](#setup).'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_external_unrelated_link_left_intact_no_warning(self):
        body = 'Read the [docs](https://example.com/page).'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_intra_workshop_sibling_link_untouched_no_warning(self):
        """Sibling ``10-qa.md`` is the intra-workshop pass's job, not ours."""
        body = '[Q&A](10-qa.md)'
        errors = []
        result = self._rewrite(body, sync_errors=errors)
        self.assertEqual(body, result)
        self.assertEqual(errors, [])

    def test_readme_md_subpage_resolves_to_landing(self):
        body = '[r](../2026-04-21-end-to-end-agent-deployment/README.md)'
        result = self._rewrite(body)
        self.assertIn(
            '[r](/workshops/end-to-end-agent-deployment)',
            result,
        )

    def test_multiple_cross_workshop_links_in_one_body(self):
        body = (
            'See [the workshop]'
            '(../2026-04-21-end-to-end-agent-deployment/) and '
            '[the Q&A]'
            '(../2026-04-21-end-to-end-agent-deployment/10-qa.md#tmux) and '
            '[via GitHub](https://github.com/AI-Shipping-Labs/workshops/'
            'tree/main/2026/2026-04-21-end-to-end-agent-deployment).'
        )
        result = self._rewrite(body)
        self.assertIn(
            '[the workshop](/workshops/end-to-end-agent-deployment)',
            result,
        )
        self.assertIn(
            '[the Q&A]'
            '(/workshops/end-to-end-agent-deployment/tutorial/qa#tmux)',
            result,
        )
        self.assertIn(
            '[via GitHub](/workshops/end-to-end-agent-deployment)',
            result,
        )


class RewriteWorkshopMdLinksCrossWorkshopWarningSuppressionTest(TestCase):
    """Issue #526: when a cross_workshop_lookup is provided, the
    intra-workshop rewriter must NOT emit "Cross-workshop or out-of-tree
    link ... left as-is" warnings for ``..``-prefixed links — the
    cross-workshop pass picks them up.
    """

    workshop_slug = 'end-to-end-agent-deployment'
    intra_lookup = WORKSHOP_LOOKUP

    def test_warning_suppressed_when_cross_lookup_passed(self):
        body = '[over](../other-workshop/01-foo.md)'
        errors = []
        result = rewrite_workshop_md_links(
            body,
            workshop_slug=self.workshop_slug,
            page_lookup=self.intra_lookup,
            sync_errors=errors,
            cross_workshop_lookup={'foo': {'slug': 'foo'}},
        )
        # Body unchanged — the cross-workshop pass (not run here) would
        # rewrite or warn separately.
        self.assertEqual(body, result)
        # No double-warning.
        self.assertEqual(errors, [])

    def test_warning_still_emitted_when_no_cross_lookup(self):
        body = '[over](../other-workshop/01-foo.md)'
        errors = []
        rewrite_workshop_md_links(
            body,
            workshop_slug=self.workshop_slug,
            page_lookup=self.intra_lookup,
            sync_errors=errors,
        )
        # Backward compat: the legacy warning fires when no cross lookup
        # is supplied (older callers / tests).
        self.assertEqual(len(errors), 1)
        self.assertIn('Cross-workshop', errors[0]['error'])
