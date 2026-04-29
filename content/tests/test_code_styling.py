"""Rendered code markup contract tests.

These tests avoid pinning exact CSS declarations from ``base.html``. The
browser-level visual coverage lives in Playwright; this Django suite keeps a
small contract that markdown code renders into distinguishable inline and block
DOM structures.
"""

from datetime import date
from html.parser import HTMLParser

from django.test import TestCase

from content.models import Article


class CodeMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.inline_code_text = []
        self.block_code_text = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if "code" not in self.stack:
            return
        if "pre" in self.stack:
            self.block_code_text.append(data)
        else:
            self.inline_code_text.append(data)


class CodeStylingDOMContractTest(TestCase):
    """Rendered content exposes separate inline-code and block-code DOM."""

    def test_markdown_code_renders_distinguishable_dom(self):
        Article.objects.create(
            title="Code DOM",
            slug="code-dom",
            date=date(2026, 4, 29),
            content_markdown=(
                "Use `uv run` for commands.\n\n"
                "```python\nprint('hello')\n```"
            ),
            published=True,
        )

        response = self.client.get("/blog/code-dom")
        self.assertEqual(response.status_code, 200)

        parser = CodeMarkupParser()
        parser.feed(response.content.decode())

        self.assertIn("uv run", "".join(parser.inline_code_text))
        self.assertIn("print('hello')", "".join(parser.block_code_text))
