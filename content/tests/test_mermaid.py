"""Unit tests for the Mermaid markdown extension (issue #300).

The extension lives in ``content/markdown_extensions/mermaid.py`` and is
wired into all five ``render_markdown`` helpers. The acceptance criteria
note that we don't need to test all five helpers — the extension is
shared, so covering one helper plus the extension's own contract is
enough. We use ``content.models.article.render_markdown`` as the
representative helper.
"""

import datetime
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from content.markdown_extensions import (
    MermaidExtension,
    MermaidPreprocessor,
)
from content.models.article import Article
from content.models.article import render_markdown as render_article_md
from content.models.course import render_markdown as render_course_md
from content.models.workshop import Workshop, WorkshopPage
from content.models.workshop import render_markdown as render_workshop_md
from events.models.event import render_markdown as render_event_md


class MermaidExtensionExportsTest(SimpleTestCase):
    """The extension module must expose the two public symbols the spec
    names. Importing them from the package init also has to work, since
    the helpers do exactly that."""

    def test_extension_class_is_importable(self):
        self.assertTrue(callable(MermaidExtension))
        self.assertTrue(callable(MermaidPreprocessor))


class RenderMermaidFenceTest(SimpleTestCase):
    """``` ```mermaid ``` ``` fences must turn into a single
    ``<div class="mermaid">…</div>`` and never reach codehilite.
    """

    def test_mermaid_fence_emits_div_mermaid(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            "    A --> B\n"
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('<div class="mermaid">', html)
        self.assertIn('flowchart LR', html)
        self.assertIn('A --&gt; B', html)
        # The </div> closes the mermaid wrapper.
        self.assertIn('</div>', html)

    def test_mermaid_fence_does_not_use_codehilite(self):
        md = "```mermaid\nflowchart LR\n    A --> B\n```\n"
        html = render_article_md(md)
        # The fenced source must NOT have been routed through the
        # syntax highlighter, otherwise we'd see Pygments wrappers
        # next to (or instead of) the mermaid div.
        self.assertNotIn('class="codehilite"', html)
        self.assertNotIn('<pre>', html)

    def test_mermaid_fence_not_wrapped_in_paragraph(self):
        """The placeholder must be in its own block — the spec requires
        the preprocessor to sandwich it with blank lines so the block
        parser doesn't wrap it in <p>."""
        md = "```mermaid\nflowchart LR\n    A --> B\n```\n"
        html = render_article_md(md)
        self.assertNotIn('<p><div class="mermaid"', html)


class RenderNonMermaidFenceTest(SimpleTestCase):
    """A fence in any other language must keep its existing codehilite
    behaviour. This is the regression guard for the rest of the site."""

    def test_python_fence_still_uses_codehilite(self):
        md = "```python\ndef greet():\n    return 'hi'\n```\n"
        html = render_article_md(md)
        self.assertIn('class="codehilite"', html)
        self.assertNotIn('<div class="mermaid">', html)

    def test_unlabelled_fence_still_uses_codehilite(self):
        md = "```\nplain text\n```\n"
        html = render_article_md(md)
        self.assertIn('class="codehilite"', html)
        self.assertNotIn('<div class="mermaid">', html)


class HtmlEscapingTest(SimpleTestCase):
    """Special characters in the mermaid source must be escaped so the
    preprocessor never emits invalid HTML or smuggles a <script> tag
    through to the browser."""

    def test_angle_brackets_are_escaped(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["<script>alert(1)</script>"] --> B\n'
            "```\n"
        )
        html = render_article_md(md)
        # Raw <script> tag must not appear anywhere in the output.
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_ampersand_is_escaped(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["A & B"] --> C\n'
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('A &amp; B', html)

    def test_double_quote_is_escaped(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["Quoted"] --> B\n'
            "```\n"
        )
        html = render_article_md(md)
        # html.escape encodes " as &quot;
        self.assertIn('&quot;Quoted&quot;', html)


class MixedContentTest(SimpleTestCase):
    """A mermaid fence and a python fence in the same document each
    render through the appropriate path."""

    def test_mixed_mermaid_and_python(self):
        md = (
            "Intro paragraph.\n\n"
            "```mermaid\n"
            "flowchart LR\n    A --> B\n"
            "```\n\n"
            "Body.\n\n"
            "```python\n"
            "x = 1\n"
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('<div class="mermaid">', html)
        self.assertIn('class="codehilite"', html)
        # Python source must be inside the codehilite block, not the mermaid div.
        self.assertNotIn('x = 1', html.split('class="codehilite"')[0])


class SharedAcrossHelpersTest(SimpleTestCase):
    """All four ``render_markdown`` helpers share the same extension
    list, so one mermaid fence rendered through each must produce the
    same div.mermaid output."""

    def test_workshop_helper_renders_mermaid(self):
        md = "```mermaid\nflowchart LR\n    A --> B\n```\n"
        self.assertIn('<div class="mermaid">', render_workshop_md(md))

    def test_course_helper_renders_mermaid(self):
        md = "```mermaid\nflowchart LR\n    A --> B\n```\n"
        self.assertIn('<div class="mermaid">', render_course_md(md))

    def test_event_helper_renders_mermaid(self):
        md = "```mermaid\nflowchart LR\n    A --> B\n```\n"
        self.assertIn('<div class="mermaid">', render_event_md(md))


class BrTagToNewlineConversionTest(SimpleTestCase):
    """``<br>`` variants inside a mermaid fence must be converted to a
    literal newline BEFORE ``html.escape`` so Mermaid 10 (with
    ``securityLevel: 'strict'``) renders the label as two lines instead
    of rejecting the diagram (issue #791).
    """

    def test_br_slash_in_node_label_becomes_newline(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["x<br/>y"]\n'
            "```\n"
        )
        html = render_article_md(md)
        # Newline lives between the escaped quotes, in place of <br/>.
        self.assertIn('A[&quot;x\ny&quot;]', html)
        # The literal tag must not survive in any form.
        self.assertNotIn('&lt;br', html)
        self.assertNotIn('<br', html)

    def test_br_no_slash_becomes_newline(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["x<br>y"]\n'
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('A[&quot;x\ny&quot;]', html)
        self.assertNotIn('&lt;br', html)

    def test_br_with_space_becomes_newline(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["x<br />y"]\n'
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('A[&quot;x\ny&quot;]', html)
        self.assertNotIn('&lt;br', html)

    def test_uppercase_br_becomes_newline(self):
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["x<BR/>y"] --> B["p<BR>q"]\n'
            "```\n"
        )
        html = render_article_md(md)
        self.assertIn('A[&quot;x\ny&quot;]', html)
        self.assertIn('B[&quot;p\nq&quot;]', html)
        self.assertNotIn('&lt;br', html)
        self.assertNotIn('&lt;BR', html)

    def test_br_outside_mermaid_fence_is_preserved(self):
        """The substitution must be scoped to the captured mermaid source
        only — a ``<br/>`` in surrounding markdown stays intact."""
        md = (
            "line1<br/>line2\n\n"
            "```mermaid\n"
            "flowchart LR\n"
            '    A["x<br/>y"] --> B\n'
            "```\n"
        )
        html = render_article_md(md)
        # The paragraph still carries the raw <br/> (python-markdown
        # passes inline HTML through unchanged).
        self.assertIn('<br/>', html)
        # The mermaid div has the newline-converted label.
        self.assertIn('<div class="mermaid">', html)
        self.assertIn('A[&quot;x\ny&quot;]', html)

    def test_existing_xss_escape_still_works_alongside_br_substitution(self):
        """The new ``<br>`` substitution must not weaken the XSS guard —
        ``<script>`` inside the fence is still escaped, and the ``<br/>``
        beside it is still translated to a newline."""
        md = (
            "```mermaid\n"
            "flowchart LR\n"
            '    A["<script>alert(1)</script><br/>tail"] --> B\n'
            "```\n"
        )
        html = render_article_md(md)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        # The <br/> between the script payload and 'tail' became a newline.
        self.assertIn('&lt;/script&gt;\ntail', html)
        self.assertNotIn('&lt;br', html)


class MermaidScriptTagInclusionTest(TestCase):
    """Lazy-load contract from issue #320: the mermaid renderer shim is
    only emitted on pages that render markdown-derived HTML. Non-content
    pages (homepage, login, studio, account, etc.) ship zero bytes of
    mermaid JavaScript."""

    def test_homepage_does_not_include_mermaid_script(self):
        response = self.client.get('/')
        body = response.content.decode()
        # Homepage extends base.html but does not render markdown body
        # content, so the partial must not be included.
        self.assertNotRegex(body, r'js/mermaid-render(\.[0-9a-f]+)?\.js')
        # Regression guard: a multi-line ``{# ... #}`` Django comment in
        # base.html leaks as visible text on every page (Django's ``{# #}``
        # is single-line only). Ensure the explanatory note about the
        # mermaid lazy-load partial never reaches the browser.
        self.assertNotIn('{# Mermaid', body)
        self.assertNotIn('lazy-loaded per page', body)

    def test_blog_detail_includes_mermaid_script(self):
        # A markdown-rendering template (``content/blog_detail.html``)
        # must include the partial via its ``extra_scripts`` block.
        Article.objects.create(
            title='Mermaid Test Post',
            slug='mermaid-test-post',
            content_markdown='# Hello\n\nBody.\n',
            date=datetime.date(2024, 1, 1),
            page_type='blog',
            published=True,
        )
        response = self.client.get('/blog/mermaid-test-post')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertRegex(body, r'js/mermaid-render(\.[0-9a-f]+)?\.js')
        # type="module" is required so the dynamic import() inside the
        # script is allowed by the browser.
        self.assertIn('type="module"', body)

    def test_workshop_tutorial_emits_mermaid_layout_rules_once_in_head(self):
        historical_workshop_date = datetime.date(2024, 1, 1)
        workshop = Workshop.objects.create(
            title='Mermaid Layout Workshop',
            slug='mermaid-layout-workshop',
            date=historical_workshop_date,
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        WorkshopPage.objects.create(
            workshop=workshop,
            title='Diagram',
            slug='diagram',
            sort_order=1,
            body=(
                '```mermaid\n'
                'flowchart LR\n'
                '    A[Start] --> B[Finish]\n'
                '```\n\nAfter the diagram.'
            ),
        )

        response = self.client.get(
            '/workshops/mermaid-layout-workshop/tutorial/diagram',
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        head, rendered_body = body.split('<body', 1)
        selector = 'div.mermaid:not([data-processed]) { white-space: pre; }'
        self.assertEqual(body.count(selector), 1)
        self.assertIn(selector, head)
        self.assertNotIn(selector, rendered_body)
        self.assertIn(
            'div.mermaid { max-width: 100%; overflow-x: auto; }',
            head,
        )
        self.assertIn(
            'div.mermaid > svg { max-width: none; height: auto; '
            'display: block; margin: 0 auto; }',
            head,
        )
        self.assertRegex(body, r'js/mermaid-render(\.[0-9a-f]+)?\.js')

        partial = Path('templates/_partials/mermaid_script.html').read_text(
            encoding='utf-8',
        )
        self.assertNotIn('<style>', partial)
