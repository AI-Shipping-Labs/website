"""The sync contract for member topic pages (issue #1688).

Mirrors the ``community_base.knowledge_base.sync`` contract against the
site-owned model:

- item identity is the page slug, unique across the app (the hub's ``index``
  slug included);
- ``source_checksum`` is the change signal; the parser folds everything the
  row derives from into it;
- synced rows carry the source's id in ``source_content_id``, which gives
  :func:`delete_missing` its ownership scope;
- a page that vanishes from the repository is drafted, not deleted;
- an empty ``commit_sha`` (disk checkouts carry the ``test-commit-sha``
  sentinel) is replaced by a stable per-page sha derived from the source
  path and checksum, the way the package provenance contract requires.
"""

import hashlib

from django.db import transaction

from content.access import LEVEL_OPEN
from topics.models import STATUS_DRAFT, STATUS_PUBLISHED, TopicPage

ACTION_CREATED = 'created'
ACTION_UPDATED = 'updated'
ACTION_UNCHANGED = 'unchanged'


def _stable_commit(source_path, checksum):
    """A 40-hex stand-in for checkouts that expose no real git commit."""

    return hashlib.sha256(f'{source_path}\0{checksum}'.encode()).hexdigest()[:40]


def upsert_topic(
    source,
    *,
    slug,
    title,
    body='',
    summary='',
    related=None,
    commit_sha,
    source_path,
    checksum,
):
    """Create, update or leave unchanged one topic page; returns ``(page, action)``.

    An unchanged page (same checksum and commit) is left completely alone
    except that a previously drafted page is republished -- its return to
    the repository is itself a change.
    """

    if not slug:
        raise ValueError('A topic page requires a slug.')
    if not title:
        raise ValueError(f'Topic page {slug!r} requires a title.')
    commit_sha = commit_sha or _stable_commit(source_path, checksum)

    with transaction.atomic():
        page = TopicPage.objects.filter(slug=slug).first()
        if page is None:
            page = TopicPage(slug=slug)
            action = ACTION_CREATED
        elif (
            page.source_checksum == checksum
            and page.source_commit_sha == commit_sha
            and page.status == STATUS_PUBLISHED
        ):
            return page, ACTION_UNCHANGED
        else:
            action = ACTION_UPDATED

        page.title = title
        page.summary = summary
        page.body = body
        page.related = list(related or [])
        # Issue #1804: the topics wiki is the free top of the funnel, so
        # every synced page is open to everyone.
        page.required_level = LEVEL_OPEN
        page.status = STATUS_PUBLISHED
        page.source_content_id = getattr(source, 'pk', None)
        page.source_path = source_path
        page.source_commit_sha = commit_sha
        page.source_checksum = checksum
        page.full_clean()
        page.save()
    return page, action


def delete_missing(source, seen_slugs):
    """Draft this source's published pages that the repository no longer lists.

    Rows synced by other sources are never touched.
    """

    missing = (
        TopicPage.objects.filter(
            status=STATUS_PUBLISHED,
            source_content_id=getattr(source, 'pk', None),
        )
        .exclude(slug__in=set(seen_slugs))
        .order_by('slug')
    )
    drafted = []
    for page in missing:
        page.status = STATUS_DRAFT
        page.save(update_fields=['status', 'updated_at'])
        drafted.append(page)
    return drafted
