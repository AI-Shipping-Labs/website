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

This module also provides :func:`detect_relative_links` (issue #1342), which
follows the same warn-never-block-never-rewrite philosophy but catches a
different class of bad link: content-repo filesystem-relative hrefs (``../``,
``./``) that are valid inside the content folder tree but 404 as site routes.
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


# Match ``<a ... href="../..">`` and ``<a ... href="./..">`` — content-repo
# filesystem-relative links (issue #1342). ``\.{1,2}/`` matches exactly a
# ``./`` or ``../`` prefix anchored to the start of the href value (right
# after the opening quote), so root-relative (``/x``), external
# (``https://``), anchor (``#``), ``mailto:`` and ``tel:`` links never match.
_RELATIVE_HREF_RE = re.compile(
    r'<a\b[^>]*?\bhref\s*=\s*'      # opening <a ... href=
    r'(?P<quote>["\'])'             # opening quote
    r'(?P<href>\.{1,2}/[^"\']*)'    # ./... or ../...
    r'(?P=quote)',                  # matching closing quote
    re.IGNORECASE,
)


def _relative_href_target_token(href):
    """Return the first meaningful path segment of a ``../``/``./`` href.

    Strips the leading run of ``../`` and ``./`` segments plus any trailing
    slash, fragment, or query, then returns the first remaining path segment.
    For ``../2099-12-31-deleted-workshop/`` this is
    ``2099-12-31-deleted-workshop`` — the folder name the workshop
    cross-workshop rewriter quotes in its "not found" warning. For a deeper
    ``../../06/2026-06-29-tailor-cv/`` link it is ``06`` — the month folder,
    which the cross-workshop rewriter never processes and never quotes.

    Returns ``''`` when no meaningful segment remains.
    """
    # Drop fragment and query so they don't leak into the segment.
    path = href.split('#', 1)[0].split('?', 1)[0]
    segments = [seg for seg in path.split('/') if seg not in ('', '.', '..')]
    return segments[0] if segments else ''


def _relative_href_already_reported(href, existing_messages):
    """True when the workshop cross-workshop rewriter already flagged ``href``.

    Issue #1342 Part B. The workshop dispatcher runs an existing cross-workshop
    link rewriter (``rewrite_cross_workshop_md_links``, issues #301/#526) BEFORE
    this detector. That rewriter handles single-level ``../<folder>/`` links: it
    either rewrites a resolvable target to a ``/workshops/...`` URL (so no ``../``
    survives for us to see) or, for an unresolvable target, leaves the link
    verbatim and records exactly one "target folder ... not found" warning that
    quotes the folder name. To avoid a duplicate warning on that same link, we
    skip an ``href`` whose first path segment is already quoted in a prior
    warning message.

    Deeper ``../../<month>/<workshop>/`` links (the original #1342 bug) are NOT
    processed by the rewriter and produce no prior warning, so their token (the
    month folder) is never quoted upstream — they still warn here, preserving
    #1342 Part B's unique value. Other dispatchers (articles/projects/events)
    have no rewriter, so ``existing_messages`` never quotes the token and nothing
    is ever skipped.
    """
    token = _relative_href_target_token(href)
    if not token:
        return False
    quoted = f'"{token}"'
    return any(quoted in message for message in existing_messages)


def detect_relative_links(html, source_path, sync_errors):
    """Scan ``html`` for content-repo-relative ``<a href>`` links and warn.

    Content authors sometimes write filesystem-relative markdown links such
    as ``[x](../../06/2026-06-29-selecting-a-portfolio-project/)``. These are
    valid paths inside the content-repo folder tree but meaningless as site
    routes — the markdown pipeline passes them through verbatim, the browser
    resolves them against the page URL, and the result is a 404 (issue #1342).
    A valid site route is always root-relative (``/...``) or an absolute URL,
    never ``../`` or ``./``.

    For each ``<a href="../...">`` or ``<a href="./...">`` match, appends one
    warning record to ``sync_errors`` of shape
    ``{'file': source_path, 'error': '...'}`` so the dispatcher surfaces it
    through the existing ``SyncLog.errors`` pipeline and the
    ``/studio/sync/`` dashboard.

    Follows the same warn-never-block-never-rewrite philosophy as
    :func:`detect_legacy_urls`: the function never raises and never modifies
    ``html``. Sync continues normally; the warning is purely advisory so the
    author can rewrite the source link to a canonical ``/...`` site URL.

    Args:
        html: Rendered article/page HTML. ``None``/empty is a no-op.
        source_path: Repo-relative path of the file being synced. Echoed in
            the warning so authors can find the offending file.
        sync_errors: The dispatcher's running list of warnings/errors,
            ultimately persisted to ``SyncLog.errors``. Pass ``None`` to
            disable side effects entirely (the helper still returns the
            list of detected hrefs in that case).

    Returns:
        list[str]: The relative hrefs found, in document order. Duplicates
        are preserved — every match emits its own warning so a page that
        repeats the same broken link surfaces both occurrences.
    """
    if not html:
        return []

    # Snapshot the messages already present so we can dedup against warnings a
    # prior sync step (the workshop cross-workshop link rewriter) emitted for
    # the same link — see :func:`_relative_href_already_reported`. Captured once
    # up front so repeated relative links in this same body still each warn.
    existing_messages = (
        [entry.get('error', '') for entry in sync_errors]
        if sync_errors else []
    )

    found = []
    for match in _RELATIVE_HREF_RE.finditer(html):
        href = match.group('href')
        found.append(href)
        if sync_errors is None:
            continue

        # Don't double-warn on a relative link the cross-workshop rewriter
        # already flagged for the same target (issue #1342 Part B).
        if _relative_href_already_reported(href, existing_messages):
            continue

        message = (
            f'Relative content-repo link "{href}" in {source_path}: '
            f'relative links (../, ./) are not valid site routes and 404 in '
            f'the browser — rewrite it to a canonical /... site URL.'
        )
        sync_errors.append({
            'file': source_path,
            'error': message,
        })

    return found
