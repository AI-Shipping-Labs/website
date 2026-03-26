import re


# Regex to match bare URLs (http or https) in HTML text content.
# Handles &amp; as part of URL (common in HTML-encoded query strings).
_URL_RE = re.compile(
    r'https?://'
    r'(?:[^\s<>\'")\]]*(?:&amp;)[^\s<>\'")\]]*)*'  # segments joined by &amp;
    r'[^\s<>\'")\]]*'
    r'[^\s<>\'")\].,;:!?]'  # must not end with punctuation
    r'|'
    r'https?://'
    r'[^\s<>\'")\]]*'
    r'[^\s<>\'")\].,;:!?]'
)

# Pattern to match regions where we should NOT linkify:
# 1. <a ...>...</a> elements (already linked)
# 2. <code>...</code> elements (inline code)
# 3. <pre>...</pre> elements (code blocks, may contain nested <code>)
# 4. Any HTML tag (e.g. <img src="...">) to avoid matching URLs in attributes
_SKIP_RE = re.compile(
    r'<a\s[^>]*>.*?</a>'
    r'|<code[^>]*>.*?</code>'
    r'|<pre[^>]*>.*?</pre>'
    r'|<[^>]+>',
    re.DOTALL,
)


def _linkify_match(match):
    """Replace a bare URL match with an <a> tag."""
    url = match.group(0)
    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
    )


def linkify_urls(html):
    """Wrap bare URLs in HTML with <a> tags.

    Skips URLs that are already inside <a>, <code>, or <pre> elements.
    Also skips URLs inside HTML tag attributes (e.g. <img src="...">, <a href="...">).
    Links open in a new tab with rel="noopener noreferrer".
    """
    # Split the HTML into protected regions (skip) and unprotected text regions.
    # Only apply URL linkification to unprotected text regions.
    result = []
    last_end = 0

    for skip_match in _SKIP_RE.finditer(html):
        start, end = skip_match.span()
        # Process the unprotected text segment before this skip region
        segment = html[last_end:start]
        result.append(_URL_RE.sub(_linkify_match, segment))
        # Append the protected region unchanged
        result.append(html[start:end])
        last_end = end

    # Process any remaining unprotected text segment
    result.append(_URL_RE.sub(_linkify_match, html[last_end:]))

    return ''.join(result)
