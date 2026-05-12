"""Detect known-legacy root-relative URLs in rendered content HTML (issue #595).

Some URL prefixes have been retired but still appear in author markdown that
predates the cutover. The most prominent example: ``/event-recordings/<slug>``
was replaced by ``/events/<slug>`` in #294 / #420, so any synced article that
still links to ``/event-recordings/...`` 404s in the browser.

This module provides :func:`detect_legacy_urls`, called from each markdown-
bodied content dispatcher (articles, projects, events/recordings, workshop
pages) right after ``content_html`` is rendered. When a legacy prefix is
spotted, the helper appends a warning record to the dispatcher's
``sync_errors`` list so it surfaces on ``SyncLog.errors`` and the
``/studio/sync/`` dashboard. The guard never raises, never rewrites, and
never blocks the sync — surfacing the warning is enough; the right fix is
for the author to clean up the source markdown.

To extend: add a new prefix (with trailing slash) to
:data:`LEGACY_URL_PATTERNS` and the matching ``replacement_hint`` in
:data:`LEGACY_URL_REPLACEMENTS`. The helper picks them up automatically.
"""

import re

# Module-level constant so adding a new legacy prefix is a one-line change.
# Each entry is a root-relative path PREFIX (must start with ``/`` and end
# with ``/`` so we match whole path segments rather than substrings).
LEGACY_URL_PATTERNS = (
    '/event-recordings/',
)


# Optional hint shown in the warning message so authors know what to fix.
# Keys must match :data:`LEGACY_URL_PATTERNS` entries exactly. Missing keys
# fall back to a generic "should be updated" message.
LEGACY_URL_REPLACEMENTS = {
    '/event-recordings/': '/events/',
}


# Build one regex that finds every <a href="/<legacy-prefix>...">. We match
# the href attribute value (single or double quoted) and capture the full
# href so the warning can echo the exact path the author wrote, including
# any sub-slug and fragment. The pattern is anchored on ``<a `` to avoid
# false positives in surrounding markup.
def _build_legacy_href_re(patterns):
    """Compile a regex matching ``<a ... href="/<prefix>...">`` for ``patterns``."""
    if not patterns:
        # An empty alternation would match every ``<a href="/">`` link; bail
        # out with a never-matches pattern instead so callers can disable
        # the guard by clearing LEGACY_URL_PATTERNS.
        return re.compile(r'(?!x)x')
    # ``re.escape`` so prefixes containing regex metacharacters (none today,
    # but future-proof) don't blow up the compile.
    alternation = '|'.join(re.escape(p.lstrip('/')) for p in patterns)
    return re.compile(
        r'<a\b[^>]*?\bhref\s*=\s*'      # opening <a ... href=
        r'(?P<quote>["\'])'             # opening quote
        r'(?P<href>/(?:' + alternation + r')[^"\']*)'  # /<legacy>...
        r'(?P=quote)',                  # matching closing quote
        re.IGNORECASE,
    )


_LEGACY_HREF_RE = _build_legacy_href_re(LEGACY_URL_PATTERNS)


def detect_legacy_urls(html, source_path, sync_errors):
    """Scan ``html`` for legacy root-relative ``<a href>`` links and warn.

    Walks ``html`` for ``<a href="/<legacy-prefix>...">`` matches against
    every entry in :data:`LEGACY_URL_PATTERNS`. For each match, appends one
    warning record to ``sync_errors`` of shape
    ``{'file': source_path, 'error': '...'}`` so the dispatcher surfaces it
    through the existing ``SyncLog.errors`` pipeline and the
    ``/studio/sync/`` dashboard.

    The function never raises and never modifies ``html``. Sync continues
    normally; the warning is purely advisory for content authors.

    Args:
        html: Rendered article/page HTML. ``None``/empty is a no-op.
        source_path: Repo-relative path of the file being synced (e.g.
            ``blog/foo.md``). Echoed in the warning so authors can find
            the offending file.
        sync_errors: The dispatcher's running list of warnings/errors,
            ultimately persisted to ``SyncLog.errors``. Pass ``None`` to
            disable side effects entirely (the helper still returns the
            list of detected URLs in that case).

    Returns:
        list[str]: The legacy URLs found, in document order. Duplicates
        are preserved — every match emits its own warning so a page that
        repeats the same broken link surfaces both occurrences.
    """
    if not html:
        return []

    found = []
    for match in _LEGACY_HREF_RE.finditer(html):
        href = match.group('href')
        found.append(href)
        if sync_errors is None:
            continue

        # Pick the matching prefix so the warning can suggest the
        # replacement. ``startswith`` order doesn't matter today (only one
        # prefix), but a future prefix that is itself a prefix of another
        # would resolve to the longest match.
        matched_prefix = ''
        for prefix in LEGACY_URL_PATTERNS:
            if href.startswith(prefix) and len(prefix) > len(matched_prefix):
                matched_prefix = prefix

        replacement = LEGACY_URL_REPLACEMENTS.get(matched_prefix, '')
        if replacement:
            message = (
                f'Legacy URL pattern {matched_prefix}... in {source_path}: '
                f'link "{href}" should be {replacement}{href[len(matched_prefix):]}'
            )
        else:
            message = (
                f'Legacy URL pattern {matched_prefix}... in {source_path}: '
                f'link "{href}" should be updated.'
            )
        sync_errors.append({
            'file': source_path,
            'error': message,
        })

    return found
