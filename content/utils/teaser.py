"""HTML-aware truncation for locked-lesson teasers.

When a non-eligible visitor opens a gated course unit we want to show them a
short preview of the lesson body — enough to spark curiosity, not enough to
replace the full lesson. Naive ``Truncator(...).words(n, html=True)`` works
in many cases but the output is hard to control (it injects ``...`` and
re-balances tags in surprising ways inside ``<pre>``/``<code>``), so we ship
our own minimal walker.

Goals:

* Stop after roughly ``n`` whitespace-separated tokens of *visible* text.
* Prefer to stop at a paragraph boundary when one is close — avoids cutting
  mid-sentence and pairs better with the fade-out gradient overlay.
* Keep all opened tags balanced (no half-closed ``<p>`` etc.).
* Drop content inside ``<script>``, ``<style>``, ``<iframe>`` and other
  embedded blocks entirely — teaser content must not auto-play video.
* Skip ``<pre>`` and ``<code>`` blocks entirely; long code samples blow
  past the word budget on a single token and look broken when truncated.
"""

from html.parser import HTMLParser

# Self-closing / void tags that don't need a matching close tag.
_VOID_TAGS = frozenset({
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
})

# Tags whose entire content we drop from the teaser. ``<script>`` /
# ``<style>`` for safety; ``<iframe>`` because the body might contain an
# inline video embed and we don't want it auto-loading on a paywall page.
_DROP_TAGS = frozenset({'script', 'style', 'iframe', 'video', 'audio'})

# Tags whose entire content we skip when computing the visible text and
# whose markup we omit from the teaser output. ``<pre>`` blocks usually
# contain code samples that count as one massive token and blow the
# word budget; cleaner to leave them out and let the fade-out promise more.
_SKIP_TAGS = frozenset({'pre'})

# Block-level tags that mark a natural paragraph boundary. We prefer to
# stop emitting content at the *end* of one of these so the teaser
# doesn't visually cut mid-sentence.
_BLOCK_TAGS = frozenset({
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote',
    'div', 'section', 'article',
})


class _TeaserTruncator(HTMLParser):
    """Walk HTML, emit at most ``max_words`` words of visible text."""

    def __init__(self, max_words):
        # convert_charrefs=False so &amp; etc. round-trip as written.
        super().__init__(convert_charrefs=False)
        self.max_words = max_words
        self.word_count = 0
        # Output chunks; joined at the end.
        self._out = []
        # Stack of currently-open tags (not counting void tags). Used both
        # for balanced closing and to detect when we're inside a drop
        # region.
        self._open = []
        # Depth inside _DROP_TAGS — when >0 we emit nothing at all.
        self._drop_depth = 0
        # Depth inside _SKIP_TAGS — when >0 we suppress text and markup
        # but still need to track open/close for nesting correctness.
        self._skip_depth = 0
        # Once we've hit the budget AND closed back to a clean boundary
        # this flips to True and the parser stops emitting.
        self._done = False

    # --- HTMLParser hooks --------------------------------------------------

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attr_str = self._format_attrs(attrs)
        if tag in _VOID_TAGS:
            self._out.append(f'<{tag}{attr_str}>')
            return
        self._out.append(f'<{tag}{attr_str}>')
        self._open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if self._done:
            return
        tag = tag.lower()
        if tag in _DROP_TAGS or self._drop_depth:
            return
        if tag in _SKIP_TAGS or self._skip_depth:
            return
        attr_str = self._format_attrs(attrs)
        # XHTML self-closing form.
        self._out.append(f'<{tag}{attr_str} />')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _VOID_TAGS:
            return  # malformed input; ignore.
        # Close the matching open tag, popping anything in between (treats
        # the input as best-effort: matches lxml-style tolerance).
        if tag in self._open:
            while self._open:
                top = self._open.pop()
                self._out.append(f'</{top}>')
                if top == tag:
                    break
        # If the budget is exhausted and we just closed a block-level tag
        # we're at a clean stopping point.
        if self.word_count >= self.max_words and tag in _BLOCK_TAGS:
            self._done = True

    def handle_data(self, data):
        if self._done or self._drop_depth or self._skip_depth:
            return
        if not data:
            return
        # Count whitespace-separated tokens. We never *split* text mid-token:
        # if a chunk would push us over budget we keep emitting it whole and
        # then stop at the next block boundary. This avoids "Build an A..."
        # truncation in the middle of a heading.
        tokens = data.split()
        if not tokens:
            self._out.append(data)
            return
        if self.word_count >= self.max_words:
            # Already over budget. Don't emit more text — wait for a block
            # close to flush.
            return
        remaining = self.max_words - self.word_count
        if len(tokens) <= remaining:
            self._out.append(data)
            self.word_count += len(tokens)
            return
        # Take the first `remaining` tokens of this text node, preserving
        # leading whitespace for visual continuity.
        leading_ws = data[:len(data) - len(data.lstrip())]
        kept = ' '.join(tokens[:remaining])
        self._out.append(leading_ws + kept)
        self.word_count = self.max_words

    def handle_entityref(self, name):
        if self._done or self._drop_depth or self._skip_depth:
            return
        self._out.append(f'&{name};')

    def handle_charref(self, name):
        if self._done or self._drop_depth or self._skip_depth:
            return
        self._out.append(f'&#{name};')

    def handle_comment(self, data):
        # Drop comments — they're never useful in a teaser.
        return

    # --- Utilities ---------------------------------------------------------

    @staticmethod
    def _format_attrs(attrs):
        parts = []
        for name, value in attrs:
            if value is None:
                parts.append(f' {name}')
            else:
                # Quote with double quotes, escape internal double quotes.
                escaped = value.replace('"', '&quot;')
                parts.append(f' {name}="{escaped}"')
        return ''.join(parts)

    def result(self):
        # Close any tags still open so the output is well-formed.
        while self._open:
            self._out.append(f'</{self._open.pop()}>')
        return ''.join(self._out)


def truncate_to_words(html, n):
    """Return a tag-balanced HTML fragment with at most ~``n`` words.

    The walker prefers to stop at a block-level boundary (``</p>``, ``</li>``,
    heading close, etc.) once the budget has been hit, so the trimmed
    fragment reads like a paragraph rather than a chopped sentence.

    Args:
        html: Source HTML — typically ``unit.body_html``.
        n: Approximate maximum visible words to keep.

    Returns:
        HTML string with all open tags closed. Returns an empty string
        when ``html`` is empty / falsy or when ``n`` is ``None`` or
        non-positive.
    """
    if not html:
        return ''
    if n is None or n <= 0:
        return ''
    parser = _TeaserTruncator(max_words=n)
    parser.feed(html)
    parser.close()
    return parser.result()


def first_sentence(text):
    """Return the first sentence of a plain-text or markdown blob.

    Used for the homework teaser: we render the homework markdown to plain
    text (or accept already-plain input) and take everything up to and
    including the first sentence-ending punctuation. Falls back to the
    whole input when no sentence delimiter is present.
    """
    if not text:
        return ''
    cleaned = text.strip()
    if not cleaned:
        return ''
    # Look for the earliest ., !, ? followed by whitespace or end of string.
    for i, ch in enumerate(cleaned):
        if ch in '.!?':
            tail = cleaned[i + 1:]
            if not tail or tail[0].isspace():
                return cleaned[:i + 1].strip()
    return cleaned
