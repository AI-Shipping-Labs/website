"""Download sync dispatcher."""

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

def _dispatch_downloads(source, repo_dir, file_list, commit_sha, stats):
    """Walker dispatch handler: process download YAML files.

    ``file_list`` is the set of repo-relative ``.yaml``/``.yml`` paths
    under any ``downloads/`` subtree, classified by
    ``_classify_repo_files``.
    """
    from content.models import Download

    seen_slugs = set()
    failed_slugs = set()

    for rel_path in file_list:
        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)

        try:
            data = _parse_yaml_file(filepath)
            slug = data.get('slug', os.path.splitext(filename)[0])

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(data, 'download', rel_path)

            # Require content_id in frontmatter
            download_content_id = data.get('content_id')
            if not download_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Edge Case 2: Slug collision across sources
            if _check_slug_collision(Download, slug, source.repo_name, rel_path):
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f"Slug collision: '{slug}' already exists from a "
                        f"different source. Skipped."
                    ),
                })
                failed_slugs.add(slug)
                continue

            seen_slugs.add(slug)

            defaults = {
                'title': data.get('title', slug),
                'description': data.get('description', ''),
                'file_url': data.get('file_url', ''),
                'file_type': data.get('file_type', 'other'),
                'file_size_bytes': data.get('file_size_bytes', 0),
                'cover_image_url': rewrite_cover_image_url(
                    data.get('cover_image', '') or data.get('cover_image_url', ''),
                    source, rel_path,
                ),
                'tags': data.get('tags', []),
                'required_level': data.get('required_level', 0),
                'published': True,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': download_content_id,
            }

            # Issue #225: only count as 'updated' when content actually changed.
            try:
                download = Download.objects.get(
                    slug=slug, source_repo=source.repo_name,
                )
            except Download.DoesNotExist:
                download = Download(slug=slug, **defaults)
                download.save()
                created = True
                changed = True
            else:
                if _defaults_differ(download, defaults):
                    for k, v in defaults.items():
                        setattr(download, k, v)
                    download.save()
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
                'slug': slug,
                'action': action,
                'content_type': 'resource',
            })

        except Exception as e:
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = data.get('slug', fallback_slug)
            except Exception:
                failed_slug = fallback_slug
            failed_slugs.add(failed_slug)
            stats['errors'].append({'file': rel_path, 'error': str(e)})

    # Soft-delete stale downloads, excluding failed slugs
    stale = Download.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    for dl in stale:
        stats['items_detail'].append({
            'title': dl.title,
            'slug': dl.slug,
            'action': 'deleted',
            'content_type': 'resource',
        })
    deleted_count = stale.count()
    stale.update(published=False)
    stats['deleted'] += deleted_count


