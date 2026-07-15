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

from django import template

from content.utils.markdown import markdown_to_plain_text

register = template.Library()

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
