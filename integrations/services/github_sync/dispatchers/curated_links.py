"""Curated link sync dispatcher."""

# ruff: noqa

import os
import re
import uuid

from django.utils import timezone
import yaml

from integrations.services.github_sync.common import INSTRUCTOR_ID_RE, GitHubSyncError, logger
from integrations.services.github_sync.media import rewrite_cover_image_url, rewrite_image_urls, _check_broken_image_refs
from integrations.services.github_sync.parsing import (
    _check_slug_collision,
    _compute_content_hash,
    _defaults_differ,
    _derive_readme_content_id,
    _derive_workshop_page_content_id,
    _extract_readme_title,
    _parse_markdown_file,
    _parse_yaml_file,
    _render_event_recap_file,
    _validate_frontmatter,
)
from integrations.services.github_sync.repo import derive_slug, extract_sort_order, _matches_ignore_patterns

def _dispatch_curated_links(source, repo_dir, file_list, commit_sha, stats):
    """Walker dispatch handler: process curated link markdown/YAML files.

    ``file_list`` is the set of repo-relative ``.md`` paths under any
    ``curated-links/`` subtree, classified by ``_classify_repo_files``.
    """
    from content.models import CuratedLink

    seen_item_ids = set()
    failed_item_ids = set()

    for rel_path in file_list:
        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)
        metadata = {}

        try:
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.md':
                metadata, body = _parse_markdown_file(filepath)
                _sync_curated_link_markdown(
                    CuratedLink, source, rel_path, metadata, body, commit_sha,
                    stats, seen_item_ids,
                )
            elif ext in ('.yaml', '.yml'):
                _sync_curated_link_manifest(
                    CuratedLink, source, filepath, rel_path, commit_sha, stats,
                    seen_item_ids, failed_item_ids,
                )

        except Exception as e:
            fallback_id = os.path.splitext(filename)[0]
            try:
                failed_id = metadata.get('item_id', fallback_id)
            except Exception:
                failed_id = fallback_id
            failed_item_ids.add(failed_id)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale links from this repo, excluding failed items
    stale = CuratedLink.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(item_id__in=seen_item_ids).exclude(item_id__in=failed_item_ids)
    for link in stale:
        stats['items_detail'].append({
            'title': link.title,
            'slug': link.item_id,
            'action': 'deleted',
            'content_type': 'resource',
        })
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


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


def _sync_curated_link_manifest(
    model, source, filepath, rel_path, commit_sha, stats,
    seen_item_ids, failed_item_ids,
):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        stats['errors'].append({
            'file': rel_path,
            'error': f'Failed to parse {os.path.basename(filepath)}: {exc}',
        })
        return

    if data is None:
        data = []
    if not isinstance(data, list):
        stats['errors'].append({
            'file': rel_path,
            'error': (
                f'Invalid curated-link manifest in {rel_path}: expected a '
                f'top-level list, got {type(data).__name__}'
            ),
        })
        return

    for index, entry in enumerate(data):
        entry_file = f'{rel_path}[{index}]'
        if not isinstance(entry, dict):
            stats['errors'].append({
                'file': entry_file,
                'error': (
                    'Invalid curated-link manifest entry: expected a mapping, '
                    f'got {type(entry).__name__}'
                ),
            })
            continue

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
            continue

        seen_item_ids.add(item_id)
        failed_item_ids.discard(item_id)
        defaults = _curated_link_defaults(
            source, rel_path, metadata, metadata.get('description', ''),
            commit_sha,
        )
        _upsert_curated_link(model, item_id, defaults, stats)


def _curated_link_defaults(source, rel_path, metadata, description, commit_sha):
    return {
        'title': metadata.get('title', ''),
        'description': description or '',
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
