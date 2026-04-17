"""Strip duplicate leading H1 from synced markdown bodies.

Authors commonly start the body of a unit / article / module README with an
H1 that repeats the frontmatter title:

    ---
    title: Running examples
    ---

    # Running examples

    This course uses two running examples.

The page templates already render the frontmatter title as the page heading,
so the reader sees the same title twice. To fix this at the sync layer we
strip the leading H1 when its text matches the title under whitespace- and
case-tolerant comparison (also tolerant of trailing punctuation like a
period or colon).

If the leading H1 differs from the title — i.e. the author chose a real
section heading as the first line — we leave the body alone.
"""
import re

# ATX H1: a single ``#`` followed by a space, capturing the heading text.
# We deliberately exclude H2+ (``##``, ``###``…) and the rare Setext form
# (``Title\n=====``) to keep the rule narrow and surprise-free.
_LEADING_H1_RE = re.compile(r'^(?P<hash>#)[ \t]+(?P<text>.+?)[ \t]*#*[ \t]*$')

# Trailing punctuation we treat as cosmetic when comparing the H1 text to
# the title. ``.`` ``:`` ``!`` ``?`` ``,`` covers the common cases without
# being so greedy that we strip meaningful characters.
_TRAILING_PUNCT_RE = re.compile(r'[.,:;!?]+$')

# Collapse runs of internal whitespace to a single space.
_WHITESPACE_RE = re.compile(r'\s+')


def _normalise(text):
    """Lower-case, collapse internal whitespace, strip trailing punctuation."""
    if text is None:
        return ''
    text = text.strip()
    text = _WHITESPACE_RE.sub(' ', text)
    text = _TRAILING_PUNCT_RE.sub('', text).strip()
    return text.lower()


def strip_leading_title_h1(body, title):
    """Return ``body`` with its leading H1 removed if it matches ``title``.

    The H1 is only stripped when:

    1. The first non-blank line of ``body`` is an ATX H1 (``# ...``).
    2. Its text matches ``title`` after normalisation
       (case-insensitive, whitespace-collapsed, trailing punctuation
       ignored on both sides).

    In every other case (no heading, leading heading is H2+, leading
    heading text differs from the title, body is empty) ``body`` is
    returned unchanged.

    Args:
        body: Raw markdown body, frontmatter already stripped.
        title: The frontmatter title that the page template renders as the
            page heading.

    Returns:
        The (possibly trimmed) markdown body.
    """
    if not body or not title:
        return body

    target = _normalise(title)
    if not target:
        return body

    lines = body.splitlines(keepends=True)

    # Skip leading blank lines but remember where the first content starts.
    idx = 0
    while idx < len(lines) and lines[idx].strip() == '':
        idx += 1

    if idx == len(lines):
        return body  # entirely blank body, nothing to strip.

    match = _LEADING_H1_RE.match(lines[idx].rstrip('\r\n'))
    if not match:
        return body  # first content line isn't an H1 — leave it alone.

    if _normalise(match.group('text')) != target:
        return body  # H1 differs from the title — author meant it, keep it.

    # Drop the H1 line, plus a single immediately-following blank line so
    # the body doesn't start with an awkward blank paragraph.
    drop_to = idx + 1
    if drop_to < len(lines) and lines[drop_to].strip() == '':
        drop_to += 1

    remainder = ''.join(lines[drop_to:])
    return remainder
