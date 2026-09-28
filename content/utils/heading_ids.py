"""Section anchors: stable ``id`` attributes on rendered headings (issue #1833).

One helper serves both the render path (``render_markdown`` runs it over the
freshly converted HTML) and the data-migration backfill of stored HTML, so a
heading sequence gets identical ids whichever path produced it.

Rules:

- Every ``h1``..``h6`` without an ``id`` gets one.
- Slug: ``markdown.extensions.toc.slugify(text, '-')`` over the heading's text
  content (tags stripped, entities unescaped). ``Streaming Responses`` ->
  ``streaming-responses``; ``Café & RAG`` -> ``cafe-rag``.
- A heading whose slug is empty (emoji or punctuation only) gets ``section``.
- Ids already present anywhere in the document (author ``{#custom}`` ids,
  raw-HTML ids, annotation headings) are kept and count as taken.
- Dedupe is GitHub style with a hyphen: ``example``, ``example-1``,
  ``example-2``.
- Everything outside the opening heading tag is left byte-for-byte
  unchanged, and a second run is a no-op (idempotent).

No permalink markup is emitted here: the hover ``#`` link is added
client-side by ``static/js/section-anchors.js`` so stored HTML, excerpts,
plain text and emails never contain it.
"""

import html as html_lib
import re

from django.db.models import Q
from markdown.extensions.toc import slugify

EMPTY_SLUG_FALLBACK = 'section'

_HEADING_RE = re.compile(
    r'<h(?P<level>[1-6])(?P<attrs>\s[^>]*)?>(?P<body>.*?)</h(?P=level)\s*>',
    re.IGNORECASE | re.DOTALL,
)
# ``\s`` before ``id`` keeps ``data-id=`` / ``aria-labelledby=`` from matching.
_ID_ATTR_RE = re.compile(
    r'''\sid\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"'>]+))''',
    re.IGNORECASE,
)
_TAG_WITH_ID_RE = re.compile(r'<[a-zA-Z][^>]*?\sid\s*=[^>]*>')
_TAG_RE = re.compile(r'<[^>]*>')


def heading_slug(text):
    """Return the base slug for a heading's text content."""
    return slugify(text, '-') or EMPTY_SLUG_FALLBACK


def heading_text(inner_html):
    """Visible text of a rendered heading's inner HTML."""
    return html_lib.unescape(_TAG_RE.sub('', inner_html))


def _id_from_attrs(attrs):
    match = _ID_ATTR_RE.search(attrs or '')
    if match is None:
        return None
    return html_lib.unescape(
        match.group('dq') or match.group('sq') or match.group('bare') or ''
    )


def _existing_ids(html):
    ids = set()
    for tag in _TAG_WITH_ID_RE.finditer(html):
        value = _id_from_attrs(tag.group(0))
        if value:
            ids.add(value)
    return ids


def add_heading_ids(html):
    """Add a slug ``id`` to every ``h1``..``h6`` in ``html`` that lacks one."""
    if not html or '<h' not in html.lower():
        return html

    taken = _existing_ids(html)

    def replace(match):
        attrs = match.group('attrs') or ''
        if _id_from_attrs(attrs) is not None:
            return match.group(0)
        base = heading_slug(heading_text(match.group('body')))
        candidate = base
        suffix = 0
        while candidate in taken:
            suffix += 1
            candidate = f'{base}-{suffix}'
        taken.add(candidate)
        whole = match.group(0)
        # Only the opening tag changes: ``id`` goes first, the original tag
        # name casing and attributes follow, and the body and closing tag
        # are copied verbatim.
        body_offset = match.start('body') - match.start(0)
        return f'<{whole[1:3]} id="{candidate}"{attrs}>{whole[body_offset:]}'

    return _HEADING_RE.sub(replace, html)


def backfill_heading_ids(model, field_names, *, using='default', label=''):
    """Add missing heading ids to stored HTML fields without calling ``save()``.

    Used by the #1833 data migrations. ``save()`` would re-render and drop
    sync-time post-processing (article/marketing include expansion, recap
    includes), so each changed row is written back with a queryset
    ``update()`` of just the changed fields. Rows whose fields contain no
    ``<h`` are never loaded for writing. Returns the number of rows updated.
    """
    manager = model._default_manager.db_manager(using)
    has_heading = Q()
    for field_name in field_names:
        has_heading |= Q(**{f'{field_name}__icontains': '<h'})
    queryset = manager.filter(has_heading).only('pk', *field_names)
    total = queryset.count()
    name = label or model.__name__
    updated = 0
    scanned = 0
    for row in queryset.iterator():
        scanned += 1
        changes = {}
        for field in field_names:
            current = getattr(row, field) or ''
            new_value = add_heading_ids(current)
            if new_value != current:
                changes[field] = new_value
        if changes:
            manager.filter(pk=row.pk).update(**changes)
            updated += 1
        if scanned % 100 == 0:
            print(
                f'  [backfill_heading_ids] {name}: scanned {scanned}/{total}',
                flush=True,
            )
    if not total:
        return 0
    print(
        f'  [backfill_heading_ids] {name}: updated {updated} of {total} '
        f'rows with headings',
        flush=True,
    )
    return updated
