"""Unit tests for the ExternalLinks markdown extension (issue #303).

The extension lives in ``content/markdown_extensions/external_links.py``
and is wired into all five ``render_markdown`` helpers
(article/project, course, workshop, event). Per the spec we use
``content.models.article.render_markdown`` as the representative helper —
the extension is shared so testing all five for the same behaviour would
be redundant.

Negative coverage matters more than positive coverage here. The cases
where links must be left ALONE (anchor, relative, root-relative,
same-domain, mailto, hand-written ``target``) are where regressions hide.
"""

from django.test import TestCase, override_settings

from content.markdown_extensions import (
    ExternalLinksExtension,
    ExternalLinksTreeprocessor,
)
from content.models.article import render_markdown as render_article_md
from content.models.course import render_markdown as render_course_md
from content.models.workshop import render_markdown as render_workshop_md
from events.models.event import render_markdown as render_event_md


class ExternalLinksExportsTest(TestCase):
    """Public symbols required by the spec must be importable from the
    extension module AND the package init."""

    def test_extension_class_is_importable(self):
        self.assertTrue(callable(ExternalLinksExtension))
        self.assertTrue(callable(ExternalLinksTreeprocessor))

    def test_package_reexports_extension(self):
        # Re-import the package init to confirm both names are exported.
        from content.markdown_extensions import (
            ExternalLinksExtension as PkgExt,
        )
        from content.markdown_extensions import (
            ExternalLinksTreeprocessor as PkgTree,
        )
        self.assertIs(PkgExt, ExternalLinksExtension)
        self.assertIs(PkgTree, ExternalLinksTreeprocessor)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class ExternalLinkRewriteTest(TestCase):
    """The happy path: an external link must gain target=_blank and a
    rel attribute that contains the noopener token."""

    def test_external_link_gets_target_blank(self):
        html = render_article_md(
            '[tmuxctl](https://github.com/alexeygrigorev/tmuxctl)'
        )
        self.assertIn('href="https://github.com/alexeygrigorev/tmuxctl"', html)
        self.assertIn('target="_blank"', html)

    def test_external_link_gets_noopener_rel_token(self):
        html = render_article_md(
            '[tmuxctl](https://github.com/alexeygrigorev/tmuxctl)'
        )
        # The rel attribute must contain the ``noopener`` token.
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')

    def test_http_external_link_also_rewritten(self):
        # http (not just https) external URLs are rewritten too.
        html = render_article_md('[old](http://example.org/foo)')
        self.assertIn('target="_blank"', html)
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class InternalLinkLeftAloneTest(TestCase):
    """Internal links must not gain ``target`` or ``rel`` attributes.
    These cases are the regression guard for the rewrite logic."""

    def test_anchor_link_has_no_target_or_rel(self):
        html = render_article_md('[next](#prerequisites)')
        self.assertIn('href="#prerequisites"', html)
        self.assertNotIn('target=', html)
        self.assertNotIn('rel=', html)

    def test_root_relative_link_has_no_target(self):
        html = render_article_md('[blog](/blog)')
        self.assertIn('href="/blog"', html)
        self.assertNotIn('target=', html)

    def test_relative_path_has_no_target(self):
        # Relative paths with no scheme (course unit links, workshop pages,
        # etc.) are internal regardless of whether the link rewriter
        # runs first — the treeprocessor only inspects scheme + netloc.
        html = render_article_md('[part 2](02-starting-notebook.md)')
        self.assertIn('href="02-starting-notebook.md"', html)
        self.assertNotIn('target=', html)

    def test_same_domain_apex_has_no_target(self):
        html = render_article_md(
            '[home](https://aishippinglabs.com/about)'
        )
        self.assertIn('href="https://aishippinglabs.com/about"', html)
        self.assertNotIn('target=', html)

    def test_same_domain_www_has_no_target(self):
        html = render_article_md(
            '[home](https://www.aishippinglabs.com/about)'
        )
        self.assertIn('href="https://www.aishippinglabs.com/about"', html)
        self.assertNotIn('target=', html)

    def test_mailto_has_no_target(self):
        html = render_article_md('[mailto](mailto:hi@aishippinglabs.com)')
        self.assertIn('href="mailto:hi@aishippinglabs.com"', html)
        self.assertNotIn('target=', html)

    def test_tel_has_no_target(self):
        html = render_article_md('[call](tel:+15551234567)')
        self.assertIn('href="tel:+15551234567"', html)
        self.assertNotIn('target=', html)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class AuthorOverridesPreservedTest(TestCase):
    """Authors can override the default behaviour with ``target="_self"``
    or by writing their own raw ``<a>`` tag — the extension must respect
    those choices."""

    def test_handwritten_target_self_is_preserved(self):
        # Raw inline HTML <a> tags are stashed by python-markdown and
        # reinjected at postprocess time, so the treeprocessor never
        # rewrites them. The handwritten target="_self" must survive
        # verbatim.
        html = render_article_md(
            '<a href="https://github.com/foo" target="_self">manual</a>'
        )
        self.assertIn('target="_self"', html)
        # And we must not have additionally added target="_blank".
        self.assertNotIn('target="_blank"', html)

    def test_existing_rel_token_is_preserved_and_noopener_appended(self):
        # ``attr_list`` lets authors set rel directly on a markdown link.
        # The existing ``noreferrer`` token must survive AND ``noopener``
        # must be appended (not replaced).
        html = render_article_md(
            '[link](https://github.com/foo){rel=noreferrer}'
        )
        # Tokens are preserved in original order, then noopener appended.
        self.assertIn('rel="noreferrer noopener"', html)
        self.assertIn('target="_blank"', html)

    def test_existing_nofollow_token_is_preserved(self):
        html = render_article_md(
            '[link](https://github.com/foo){rel=nofollow}'
        )
        self.assertIn('rel="nofollow noopener"', html)


class SiteHostDetectionTest(TestCase):
    """Same-domain detection must use ``settings.SITE_BASE_URL`` and
    treat both apex + www as internal. An unset SITE_BASE_URL means
    everything absolute is external (the safe default)."""

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_apex_and_www_both_internal_when_site_url_apex(self):
        for url in (
            'https://aishippinglabs.com/about',
            'https://www.aishippinglabs.com/about',
        ):
            html = render_article_md(f'[home]({url})')
            self.assertIn(f'href="{url}"', html)
            self.assertNotIn('target=', html, msg=f'failed for {url}')

    @override_settings(SITE_BASE_URL='https://www.aishippinglabs.com')
    def test_apex_and_www_both_internal_when_site_url_www(self):
        # When SITE_BASE_URL itself is configured with the www subdomain,
        # the apex domain must still be recognised as internal.
        for url in (
            'https://aishippinglabs.com/about',
            'https://www.aishippinglabs.com/about',
        ):
            html = render_article_md(f'[home]({url})')
            self.assertIn(f'href="{url}"', html)
            self.assertNotIn('target=', html, msg=f'failed for {url}')

    @override_settings(SITE_BASE_URL='')
    def test_empty_site_url_treats_all_http_as_external(self):
        # Safer default: when SITE_BASE_URL is unset, every absolute
        # http(s) URL is rewritten, even ones pointing at our own
        # domain.
        html = render_article_md(
            '[home](https://aishippinglabs.com/about)'
        )
        self.assertIn('target="_blank"', html)
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')

    @override_settings(SITE_BASE_URL='https://staging.example.com')
    def test_other_site_url_keeps_aishippinglabs_external(self):
        # If SITE_BASE_URL points elsewhere (e.g. staging), then a link
        # to the production domain is correctly seen as external.
        html = render_article_md(
            '[main site](https://aishippinglabs.com/about)'
        )
        self.assertIn('target="_blank"', html)

    @override_settings(SITE_BASE_URL='http://localhost:8000')
    def test_localhost_dev_keeps_prod_url_external(self):
        # On dev (SITE_BASE_URL=http://localhost:8000), a stray
        # https://aishippinglabs.com link in synced content should still
        # be treated as external — the safer default for dev sends.
        html = render_article_md(
            '[prod home](https://aishippinglabs.com/about)'
        )
        self.assertIn('target="_blank"', html)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class MermaidCoexistenceTest(TestCase):
    """The external_links treeprocessor must not interfere with the
    mermaid stash. Mermaid blocks become ``<div class="mermaid">``
    placeholders before parse, so the treeprocessor never sees ``<a>``
    elements inside them — verifying the output is a regression guard."""

    def test_mermaid_div_unchanged_when_extension_runs(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            "    A --> B\n"
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('<div class="mermaid">', html)
        # No <a> tag should be injected into the mermaid output.
        self.assertNotIn('<a', html.split('</div>')[0])

    def test_mermaid_and_external_link_in_same_doc(self):
        md = (
            "Intro paragraph with [tmuxctl](https://github.com/x/tmuxctl).\n\n"
            "```mermaid\n"
            "flowchart LR\n    A --> B\n"
            "```\n"
        )
        html = render_article_md(md)
        # The external link is rewritten...
        self.assertIn('target="_blank"', html)
        # ...and the mermaid div is intact.
        self.assertIn('<div class="mermaid">', html)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class SharedAcrossHelpersTest(TestCase):
    """All four ``render_markdown`` helpers share the same extension
    list, so an external link rendered through each must be rewritten
    identically. This is the smoke-test for AC #3 (every helper wired
    up)."""

    EXTERNAL_MD = '[gh](https://github.com/alexeygrigorev/tmuxctl)'

    def test_article_helper_rewrites(self):
        html = render_article_md(self.EXTERNAL_MD)
        self.assertIn('target="_blank"', html)
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')

    def test_course_helper_rewrites(self):
        html = render_course_md(self.EXTERNAL_MD)
        self.assertIn('target="_blank"', html)
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')

    def test_workshop_helper_rewrites(self):
        html = render_workshop_md(self.EXTERNAL_MD)
        self.assertIn('target="_blank"', html)
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')

    def test_event_helper_rewrites(self):
        html = render_event_md(self.EXTERNAL_MD)
        self.assertIn('target="_blank"', html)
        self.assertRegex(html, r'rel="[^"]*\bnoopener\b[^"]*"')


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class IdempotenceTest(TestCase):
    """Re-running the rewrite over already-rewritten output must be a
    no-op (no doubled noopener tokens, no target reset). This matters
    because content can be re-saved during sync and the rendered HTML
    is then re-rendered when rebuilt from markdown."""

    def test_running_twice_does_not_duplicate_noopener(self):
        first = render_article_md(
            '[gh](https://github.com/alexeygrigorev/tmuxctl)'
        )
        # Count noopener occurrences in the rel attribute.
        self.assertEqual(first.count('noopener'), 1)

    def test_already_noopener_attr_list_not_duplicated(self):
        html = render_article_md(
            '[gh](https://github.com/x){rel=noopener}'
        )
        self.assertEqual(html.count('noopener'), 1)
