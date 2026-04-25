"""Mermaid diagram support for python-markdown (issue #300).

Codehilite has no ``mermaid`` lexer, so a fenced ``` ```mermaid ``` ``` block
falls back to the ``text`` lexer and the ``language-mermaid`` class is
stripped from the output. That breaks the common client-side approach of
"find ``code.language-mermaid`` and run mermaid.run() on it."

This preprocessor runs BEFORE ``fenced_code``'s default priority (25) so
the mermaid fence is intercepted before codehilite ever sees it. The
diagram source is HTML-escaped and wrapped in ``<div class="mermaid">``,
then stashed via ``md.htmlStash.store(...)`` so the block parser leaves
the placeholder alone (otherwise it would be wrapped in ``<p>``).

A small client-side script (``static/js/mermaid-render.js``) lazy-loads
the Mermaid 10 ESM build from jsdelivr ONLY when at least one
``div.mermaid`` is present on the page.
"""

import html
import re

from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor


class MermaidPreprocessor(Preprocessor):
    """Replace ``` ```mermaid ``` ``` fences with a stashed ``<div class="mermaid">``.

    The regex anchors on ``^```mermaid`` and ``^```\\s*$`` (multiline) so it
    only matches *block-level* mermaid fences, not inline triple-backticks
    on the same line as other text. The captured group is the raw mermaid
    source (no leading/trailing fence lines).
    """

    PATTERN = re.compile(
        r'^```mermaid\s*\n(.*?)\n^```\s*$',
        re.DOTALL | re.MULTILINE,
    )

    def run(self, lines):
        text = '\n'.join(lines)

        def replace(match):
            source = match.group(1)
            escaped = html.escape(source)
            stashed = self.md.htmlStash.store(
                f'<div class="mermaid">{escaped}</div>'
            )
            # Surround the placeholder with blank lines so the block parser
            # treats it as its own block instead of wrapping it in <p>.
            return f'\n\n{stashed}\n\n'

        return self.PATTERN.sub(replace, text).split('\n')


class MermaidExtension(Extension):
    """Register :class:`MermaidPreprocessor` with priority 30.

    ``fenced_code``'s default priority is 25, so registering at 30 makes
    sure the mermaid fence is intercepted first and never reaches
    codehilite.
    """

    def extendMarkdown(self, md):
        md.preprocessors.register(
            MermaidPreprocessor(md),
            'mermaid',
            30,
        )
