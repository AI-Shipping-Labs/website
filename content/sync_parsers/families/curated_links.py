"""Curated link sync parser (family adapter over the moved dispatcher)."""

import os

import yaml

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import (
    checkout_read_text,
    raise_if_checkout_error,
)
from content.sync_parsers.common import logger
from content.sync_parsers.parsing import (
    _defaults_differ,
    _parse_markdown_file,
    _validate_frontmatter,
)


def _clean_curated_link_text(value):
    """Normalize sync-managed curated-link text without truncating it."""
    if value is None:
        return ''
    text = str(value)
    return text.replace('\\"', '"').replace("\\'", "'").strip()


def _sync_curated_link_markdown(
    model, source, rel_path, metadata, body, commit_sha, stats, seen_item_ids,
):
    # Edge Case 7: Frontmatter validation
    # Map content_id to item_id for validation
    if 'content_id' in metadata and 'item_id' not in metadata:
        metadata['item_id'] = metadata['content_id']

    _validate_frontmatter(metadata, 'curated_link', rel_path)

    item_id = metadata.get('item_id')
    if not item_id:
        msg = f'Skipping {rel_path}: missing content_id/item_id in frontmatter'
        logger.warning(msg)
        stats['errors'].append({'file': rel_path, 'error': msg})
        return

    seen_item_ids.add(item_id)

    # Use body text as description, fall back to frontmatter description
    description = body.strip() if body.strip() else metadata.get('description', '')

    defaults = _curated_link_defaults(
        source, rel_path, metadata, description, commit_sha,
    )
    _upsert_curated_link(model, item_id, defaults, stats)


def _sync_curated_link_entry(
    model, source, rel_path, index, entry, commit_sha, stats,
    seen_item_ids, failed_item_ids,
):
    """Validate and upsert one entry of a curated-link manifest file."""
    entry_file = f'{rel_path}[{index}]'
    if not isinstance(entry, dict):
        stats['errors'].append({
            'file': entry_file,
            'error': (
                'Invalid curated-link manifest entry: expected a mapping, '
                f'got {type(entry).__name__}'
            ),
        })
        return

    metadata = dict(entry)
    if 'content_id' in metadata and 'item_id' not in metadata:
        metadata['item_id'] = metadata['content_id']
    item_id = metadata.get('item_id')
    if item_id:
        failed_item_ids.add(item_id)

    missing = [
        field for field in ('item_id', 'title', 'url')
        if metadata.get(field) is None
        or metadata.get(field) == ''
        or metadata.get(field) == []
    ]
    if missing:
        stats['errors'].append({
            'file': entry_file,
            'error': (
                f"Invalid curated-link manifest entry"
                f"{f' {item_id}' if item_id else ''}: missing required "
                f"field(s): {', '.join(missing)}"
            ),
        })
        return

    seen_item_ids.add(item_id)
    failed_item_ids.discard(item_id)
    defaults = _curated_link_defaults(
        source, rel_path, metadata, metadata.get('description', ''),
        commit_sha,
    )
    _upsert_curated_link(model, item_id, defaults, stats)


def _curated_link_defaults(source, rel_path, metadata, description, commit_sha):
    return {
        'title': _clean_curated_link_text(metadata.get('title', '')),
        'description': _clean_curated_link_text(description or ''),
        'url': metadata.get('url', ''),
        'category': metadata.get('category', 'other'),
        'tags': metadata.get('tags', []) or [],
        'source': metadata.get('source', '') or '',
        'sort_order': metadata.get('sort_order', 0),
        'required_level': metadata.get('required_level', 0),
        'published': metadata.get('published', True),
        'source_repo': source.repo_name,
        'source_path': rel_path,
        'source_commit': commit_sha,
    }


def _upsert_curated_link(model, item_id, defaults, stats):
    # Issue #225: only count as 'updated' when content actually changed.
    try:
        obj = model.objects.get(item_id=item_id)
    except model.DoesNotExist:
        obj = model(item_id=item_id, **defaults)
        obj.save()
        created = True
        changed = True
    else:
        if _defaults_differ(obj, defaults):
            for k, v in defaults.items():
                setattr(obj, k, v)
            obj.save()
            created = False
            changed = True
        else:
            created = False
            changed = False

    if not changed:
        stats['unchanged'] += 1
        return

    action = 'created' if created else 'updated'
    if created:
        stats['created'] += 1
    else:
        stats['updated'] += 1
    stats['items_detail'].append({
        'title': defaults['title'],
        'slug': item_id,
        'action': action,
        'content_type': 'resource',
    })


class CuratedLinksParser(FamilyParser):
    content_type = 'curated_links'
    state_name = 'curated_links'

    def iter_items(self, run):
        state = self._state(run)
        for rel_path in run.classification().curated_link_files:
            filepath = os.path.join(run.repo_dir, rel_path)
            filename = os.path.basename(rel_path)
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.md':
                try:
                    metadata, body = _parse_markdown_file(filepath)
                except Exception as e:  # noqa: BLE001 - bounded per-file capture
                    raise_if_checkout_error(e)
                    state.record_error({'file': rel_path, 'error': str(e)})
                    state.failed.add(os.path.splitext(filename)[0])
                    continue
                yield rel_path, {
                    'rel_path': rel_path,
                    'kind': 'md',
                    'metadata': metadata,
                    'body': body,
                }
            elif ext in ('.yaml', '.yml'):
                try:
                    data = yaml.safe_load(checkout_read_text(filepath))
                except yaml.YAMLError as exc:
                    state.record_error({
                        'file': rel_path,
                        'error': f'Failed to parse {filename}: {exc}',
                    })
                    continue
                if data is None:
                    continue
                if not isinstance(data, list):
                    state.record_error({
                        'file': rel_path,
                        'error': (
                            f'Invalid curated-link manifest in {rel_path}: '
                            f'expected a top-level list, got {type(data).__name__}'
                        ),
                    })
                    continue
                for index, entry in enumerate(data):
                    yield f'{rel_path}[{index}]', {
                        'rel_path': rel_path,
                        'kind': 'manifest',
                        'index': index,
                        'entry': entry,
                    }

    def process(self, run, payload):
        from content.models import CuratedLink

        state = self._state(run)
        stats = self.item_stats()
        rel_path = payload['rel_path']
        commit_sha = run.commit_sha
        if payload['kind'] == 'md':
            _sync_curated_link_markdown(
                CuratedLink, run.source, rel_path, payload['metadata'],
                payload['body'], commit_sha, stats, state.seen,
            )
        else:
            _sync_curated_link_entry(
                CuratedLink, run.source, rel_path, payload['index'],
                payload['entry'], commit_sha, stats, state.seen, state.failed,
            )
        action = self.absorb(run, stats)
        return action, None

    def cleanup(self, run):
        from content.models import CuratedLink

        state = self._state(run)
        # Soft-delete stale links from this repo, excluding failed items
        stale = CuratedLink.objects.filter(
            source_repo=run.source.repo_name,
            published=True,
        ).exclude(item_id__in=state.seen).exclude(item_id__in=state.failed)
        for link in stale:
            state.details.append({
                'title': link.title,
                'slug': link.item_id,
                'action': 'deleted',
                'content_type': 'resource',
            })
        deleted_count = stale.count()
        stale.update(published=False)
        return deleted_count
