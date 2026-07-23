"""Template filters for content teasers / excerpts.

The ``strip_markdown`` filter turns a raw markdown ``description`` string into a
clean plain-text excerpt suitable for list/card teasers. List pages store the
unrendered markdown source in ``description`` for several models
(``Article``, ``Tutorial``, ``Project``, ``CuratedLink``, ``Download``) that
have no rendered ``description_html`` field, so a markdown link
``[label](url)`` or emphasis ``**bold**`` would otherwise leak its literal
syntax characters into the teaser (issue #917).

Usage in templates:

    {% load teaser_tags %}
    {{ item.description|strip_markdown|truncatechars:80 }}

Chain ``truncatechars``/``truncatewords`` AFTER ``strip_markdown`` so the
per-surface truncation length is preserved.
"""

import re

from django import template
from django.utils.safestring import mark_safe

from content.utils.markdown import markdown_to_plain_text

register = template.Library()

# A run of one or more consecutive code blocks (optionally separated by
# whitespace), collapsed to a single "Code hidden" placeholder so a gated
# teaser shows a clear locked-code affordance instead of a bare rectangle.
#
# The teaser truncator (``content.utils.teaser``) deliberately drops the
# contents of ``<pre>`` blocks, but code is wrapped in
# ``<div class="codehilite"><pre>...</pre></div>`` — so what survives into
# the teaser is an EMPTY ``<div class="codehilite"></div>`` that the syntax
# CSS styles as an empty code rectangle. We match that wrapper as well as any
# bare ``<pre>`` block.
_CODE_UNIT = r'<div class="codehilite"[^>]*>.*?</div>|<pre\b[^>]*>.*?</pre>'
_CODE_BLOCK_RUN_RE = re.compile(
    rf'(?:{_CODE_UNIT})(?:\s*(?:{_CODE_UNIT}))*',
    re.DOTALL | re.IGNORECASE,
)

_CODE_HIDDEN_PLACEHOLDER = (
    '<div class="teaser-code-hidden not-prose my-6 flex items-center gap-2 '
    'rounded-lg border border-border bg-card px-4 py-3 '
    'text-sm text-muted-foreground" data-testid="teaser-code-hidden">'
    '<i data-lucide="lock" class="h-4 w-4 flex-shrink-0"></i>'
    '<span>Code block hidden</span>'
    '</div>'
)


@register.filter
def hide_code_blocks(value):
    """Replace ``<pre>`` code blocks in gated teaser HTML with a placeholder.

    Gated teasers keep truncated body HTML; code blocks in that fragment
    render as bare rectangles with no useful content. This swaps each run of
    consecutive code blocks for a single "Code hidden — unlock to view"
    card. Returns safe HTML (the input is already-rendered, trusted body
    HTML); empty/falsy input returns ``''``.
    """
    if not value:
        return ''
    return mark_safe(_CODE_BLOCK_RUN_RE.sub(_CODE_HIDDEN_PLACEHOLDER, value))

@register.filter
def strip_markdown(value):
    """Render markdown to plain text: drop tags, unescape entities, collapse whitespace.

    A markdown link ``[label](url)`` reduces to ``label``; emphasis
    ``**bold**`` / ``_italic_`` reduces to ``bold`` / ``italic``; headings,
    code fences, and other markdown syntax characters are removed. Empty or
    falsy input returns ``''`` without raising.

    Mermaid/external-link extensions are disabled here — they add nothing to a
    plain-text excerpt and only cost render time.
    """
    return markdown_to_plain_text(value)
