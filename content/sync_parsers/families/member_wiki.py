"""Member wiki topic page sync parser (issue #1688).

One family fills the site-owned ``topics`` app from the private wiki
repository's top-level ``_wiki/`` section. The pages are the member
exploration layer: ``index.md`` is the hub rendered at ``/topics/`` and
every sibling file stem becomes a ``/topics/<slug>/`` page, gated at
Basic and above. The repository's record directories (``_workshops/``,
``_courses/``, ...) are its internal agent index and are claimed by the
classifier, so this family sees only ``_wiki/``.

Storage follows the site contract in ``topics.sync`` (the mirror of the
package knowledge-base contract); this module only parses the checkout
and applies that contract.
"""

import hashlib
import os

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import raise_if_checkout_error
from content.sync_parsers.families.knowledge_base import _package_commit_sha
from content.sync_parsers.parsing import (
    _parse_markdown_file,
    _validate_frontmatter,
)
from topics import sync


def _coerce_related(metadata, rel_path):
    """The ``related:`` frontmatter list, coerced to a list of slugs."""
    raw = metadata.get('related')
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f'{rel_path}: related must be a list of topic slugs, '
            f'got {raw!r}.'
        )
    return [str(slug) for slug in raw]


def _checksum_for(metadata, body, related):
    """The change signal: everything the row derives from."""
    payload = '|'.join((
        str(metadata.get('title') or ''),
        str(metadata.get('summary') or ''),
        body,
        '\x1f'.join(related),
    ))
    return hashlib.sha256(payload.encode()).hexdigest()


class MemberWikiPagesParser(FamilyParser):
    """Sync ``_wiki/*.md`` from the private wiki repo into the topics app."""

    content_type = 'wiki_topics'
    state_name = 'wiki_topics'
    file_list = 'member_wiki_page_files'
    detail_content_type = 'wiki_topic'

    def _slug_for(self, rel_path):
        """The page slug: the file stem directly in ``_wiki/``."""
        parts = rel_path.split(os.sep)
        if len(parts) != 2:
            raise ValueError(
                f'{rel_path} must sit directly in the _wiki/ directory '
                '(no subdirectories).'
            )
        return os.path.splitext(parts[1])[0]

    def iter_items(self, run):
        state = self._state(run)
        for rel_path in getattr(run.classification(), self.file_list):
            try:
                metadata, body = _parse_markdown_file(
                    os.path.join(run.repo_dir, rel_path),
                )
                _validate_frontmatter(metadata, self.content_type, rel_path)
                slug = self._slug_for(rel_path)
                related = _coerce_related(metadata, rel_path)
            except Exception as exc:  # noqa: BLE001 - bounded per-file capture
                raise_if_checkout_error(exc)
                state.record_error({'file': rel_path, 'error': str(exc)})
                continue
            yield rel_path, {
                'rel_path': rel_path,
                'slug': slug,
                'metadata': metadata,
                'body': body,
                'related': related,
                'checksum': _checksum_for(metadata, body, related),
                'identity': slug,
            }

    def process(self, run, payload):
        state = self._state(run)
        stats = self.item_stats()
        metadata = payload['metadata']
        page, action = sync.upsert_topic(
            run.source,
            slug=payload['slug'],
            title=str(metadata.get('title') or payload['slug']),
            body=payload['body'],
            summary=str(metadata.get('summary') or ''),
            related=payload['related'],
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
            run.source, state.seen | state.failed,
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
        from content.nav_availability import refresh_topics_nav_cache

        refresh_topics_nav_cache()
