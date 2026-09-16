"""Knowledge base sync parsers (issue #1685, A7.1).

Two families fill the shared package app
``community_base.knowledge_base`` from the content repository's top-level
``wiki/`` and ``docs/`` sections:

- ``wiki_pages``: flat standalone pages, ``wiki/<slug>.md``;
- ``docs_pages``: a documentation tree, ``docs/<slug>.md`` where the
  ``parent:`` frontmatter field names the parent page's slug and
  ``nav_order:`` positions the page among its siblings.

Storage, hierarchy resolution and rendering live in the package
(``sync.upsert_page`` / ``sync.delete_missing``); this module only parses
the checkout and applies the package contract. Slugs are the file stem
(no subdirectories): the docs URL walk resolves ancestor segments through
explicit parent links, not through directory structure, and the package
slug alphabet forbids slashes.
"""

import hashlib
import os
import re

from community_base.knowledge_base import sync
from community_base.knowledge_base.models import (
    SECTION_DOCS,
    SECTION_WIKI,
    SLUG_PATTERN,
)

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import raise_if_checkout_error
from content.sync_parsers.media import (
    _check_broken_image_refs,
    rewrite_image_urls,
)
from content.sync_parsers.parsing import (
    _parse_markdown_file,
    _validate_frontmatter,
)


def _coerce_nav_order(metadata, rel_path):
    raw = metadata.get('nav_order')
    if raw in (None, ''):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f'nav_order must be an integer in {rel_path}: {raw!r}'
        ) from exc


_FULL_COMMIT_SHA = re.compile(r'^[0-9a-f]{40}$')


def _package_commit_sha(run):
    """The commit sha the package contract accepts for one checkout.

    ``sync.upsert_page`` full-cleans the row, and the package provenance
    field validates a full lowercase Git SHA. Git checkouts pass theirs
    through; disk checkouts carry the legacy ``test-commit-sha`` sentinel,
    which is handed over as empty so the package derives its stable
    per-page SHA from the source path and checksum instead.
    """

    commit_sha = run.commit_sha or ''
    if _FULL_COMMIT_SHA.match(commit_sha):
        return commit_sha
    return ''


class KnowledgeBasePagesParser(FamilyParser):
    """Shared body for the wiki and docs parser families.

    Subclasses set ``section`` (the package section key), ``file_list``
    (the ``RepoClassification`` attribute holding this section's files),
    and ``detail_content_type`` (the items_detail content-type label).
    """

    section = ''
    file_list = ''
    detail_content_type = ''

    # -- parsing ---------------------------------------------------------------

    def _slug_for(self, rel_path):
        """The page slug: the file stem relative to the section root.

        A file in a subdirectory refuses the item: slugs are flat, and the
        documentation tree is expressed through explicit ``parent`` links.
        """
        parts = rel_path.split(os.sep)
        if len(parts) != 2:
            raise ValueError(
                f'{rel_path} must sit directly in the {self.section}/ '
                'directory (no subdirectories); use the parent: frontmatter '
                'field to nest documentation pages.'
            )
        slug = os.path.splitext(parts[1])[0]
        if not re.match(SLUG_PATTERN, slug):
            raise ValueError(
                f'{rel_path}: slug {slug!r} is invalid; use letters, digits, '
                'dots, dashes or underscores only.'
            )
        return slug

    def _parse_entry(self, run, rel_path, errors):
        """Parse one section file into ``(slug, metadata, body, parent_slug)``.

        Relative image references are rewritten to their CDN URLs the way
        every other markdown family does; a reference whose image never
        made it to the media pre-pass is logged into ``errors`` without
        refusing the page, matching the marketing-pages behavior.
        """
        metadata, body = _parse_markdown_file(
            os.path.join(run.repo_dir, rel_path),
        )
        _validate_frontmatter(metadata, self.content_type, rel_path)
        slug = self._slug_for(rel_path)
        parent_slug = metadata.get('parent') or None
        if parent_slug is not None and self.section == SECTION_WIKI:
            raise ValueError(
                f'{rel_path}: wiki pages cannot have a parent; '
                'the wiki section is flat.'
            )
        base_dir = os.path.dirname(rel_path)
        known_images = run.known_images()
        if known_images is not None:
            _check_broken_image_refs(
                body, rel_path, run.source.repo_name, base_dir,
                known_images, errors,
            )
        body = rewrite_image_urls(body, run.source.repo_name, base_dir)
        return slug, metadata, body, (
            str(parent_slug) if parent_slug is not None else None
        )

    def _checksum_for(self, metadata, body, parent_slug, nav_order):
        """The package change signal: everything the row derives from."""
        payload = '|'.join((
            str(metadata.get('title') or ''),
            str(metadata.get('summary') or ''),
            body,
            parent_slug or '',
            str(nav_order),
        ))
        return hashlib.sha256(payload.encode()).hexdigest()

    # -- FamilyParser protocol -------------------------------------------------

    def iter_items(self, run):
        """Yield this section's items with parents emitted before children.

        Per-file parse failures are recorded in the family error sink and
        skipped; a page whose ``parent`` reference never resolves (unknown
        or cyclic chain) is reported as its own bounded error rather than
        silently dropping the hierarchy.
        """
        state = self._state(run)
        entries = {}
        for rel_path in getattr(run.classification(), self.file_list):
            errors = []
            try:
                slug, metadata, body, parent_slug = self._parse_entry(
                    run, rel_path, errors,
                )
            except Exception as exc:  # noqa: BLE001 - bounded per-file capture
                raise_if_checkout_error(exc)
                state.record_error({'file': rel_path, 'error': str(exc)})
                continue
            for error in errors:
                state.record_error({'file': rel_path, 'error': error})
            entries[slug] = (rel_path, metadata, body, parent_slug)

        pending = sorted(entries)
        emitted = set()
        while pending:
            remaining = []
            progressed = False
            for slug in pending:
                parent_slug = entries[slug][3]
                if parent_slug is None or parent_slug in emitted:
                    rel_path, metadata, body, _ = entries[slug]
                    nav_order = _coerce_nav_order(metadata, rel_path)
                    yield rel_path, {
                        'rel_path': rel_path,
                        'metadata': metadata,
                        'body': body,
                        'slug': slug,
                        'parent_slug': parent_slug,
                        'nav_order': nav_order,
                        'checksum': self._checksum_for(
                            metadata, body, parent_slug, nav_order,
                        ),
                        'identity': slug,
                    }
                    emitted.add(slug)
                    progressed = True
                else:
                    remaining.append(slug)
            if not progressed:
                for slug in remaining:
                    rel_path = entries[slug][0]
                    parent_slug = entries[slug][3]
                    state.record_error({
                        'file': rel_path,
                        'error': (
                            f'parent {parent_slug!r} does not exist in '
                            f'{self.section}/ or creates a cycle'
                        ),
                    })
                    state.failed.add(slug)
                break
            pending = remaining

    def process(self, run, payload):
        state = self._state(run)
        stats = self.item_stats()
        metadata = payload['metadata']
        page, action = sync.upsert_page(
            run.source,
            section=self.section,
            slug=payload['slug'],
            title=str(metadata.get('title') or payload['slug']),
            body=payload['body'],
            summary=str(metadata.get('summary') or ''),
            parent_slug=payload['parent_slug'],
            nav_order=payload['nav_order'],
            commit_sha=_package_commit_sha(run),
            source_path=payload['rel_path'],
            checksum=payload['checksum'],
        )
        state.seen.add(payload['slug'])
        stats[action] += 1
        stats['items_detail'].append({
            'title': page.title,
            'slug': page.slug,
            'action': action,
            'content_type': self.detail_content_type,
        })
        action = self.absorb(run, stats)
        return action, page

    def cleanup(self, run):
        state = self._state(run)
        # Failed pages are still listed by the repository, so they join the
        # seen set and keep their rows out of the delete-missing sweep.
        drafted = sync.delete_missing(
            run.source, self.section, state.seen | state.failed,
        )
        stats = self.item_stats()
        for page in drafted:
            stats['items_detail'].append({
                'title': page.title,
                'slug': page.slug,
                'action': 'deleted',
                'content_type': self.detail_content_type,
            })
        stats['deleted'] += len(drafted)
        self.absorb(run, stats)
        self._refresh_nav_availability()
        return len(drafted)

    def _refresh_nav_availability(self):
        from content.nav_availability import (
            refresh_knowledge_base_nav_cache,
        )

        refresh_knowledge_base_nav_cache()


class WikiPagesParser(KnowledgeBasePagesParser):
    content_type = 'wiki_pages'
    state_name = 'wiki_pages'
    section = SECTION_WIKI
    file_list = 'wiki_page_files'
    detail_content_type = 'wiki_page'


class DocsPagesParser(KnowledgeBasePagesParser):
    content_type = 'docs_pages'
    state_name = 'docs_pages'
    section = SECTION_DOCS
    file_list = 'docs_page_files'
    detail_content_type = 'docs_page'
