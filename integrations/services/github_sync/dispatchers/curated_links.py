"""Curated link sync dispatcher."""

# ruff: noqa

import os
import re
import uuid

from django.utils import timezone

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
    """Walker dispatch handler: process curated link markdown files.

    ``file_list`` is the set of repo-relative ``.md`` paths under any
    ``curated-links/`` subtree, classified by ``_classify_repo_files``.
    """
    from content.models import CuratedLink

    seen_item_ids = set()
    failed_item_ids = set()

    for rel_path in file_list:
        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)

        try:
            metadata, body = _parse_markdown_file(filepath)

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
                continue

            seen_item_ids.add(item_id)

            # Use body text as description, fall back to frontmatter description
            description = body.strip() if body.strip() else metadata.get('description', '')

            defaults = {
                'title': metadata.get('title', ''),
                'description': description,
                'url': metadata.get('url', ''),
                'category': metadata.get('category', 'other'),
                'tags': metadata.get('tags', []),
                'sort_order': metadata.get('sort_order', 0),
                'required_level': metadata.get('required_level', 0),
                'published': metadata.get('published', True),
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            # Issue #225: only count as 'updated' when content actually changed.
            try:
                obj = CuratedLink.objects.get(item_id=item_id)
            except CuratedLink.DoesNotExist:
                obj = CuratedLink(item_id=item_id, **defaults)
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
                continue

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


