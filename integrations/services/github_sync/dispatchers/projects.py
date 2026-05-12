"""Project sync dispatcher."""

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

def _dispatch_projects(source, repo_dir, file_list, commit_sha, stats,
                       known_images=None):
    """Walker dispatch handler: process project markdown files.

    ``file_list`` is the set of repo-relative ``.md`` paths classified by
    ``_classify_repo_files`` as having both ``difficulty`` and ``author``
    frontmatter fields.
    """
    from datetime import date as date_type

    from content.models import Project

    seen_slugs = set()
    failed_slugs = set()
    metadata = {}

    for rel_path in file_list:
        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)

        try:
            metadata, body = _parse_markdown_file(filepath)
            slug = metadata.get('slug', os.path.splitext(filename)[0])

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(metadata, 'project', rel_path)

            # Require content_id in frontmatter
            project_content_id = metadata.get('content_id')
            if not project_content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Edge Case 2: Slug collision across sources
            if _check_slug_collision(Project, slug, source.repo_name, rel_path):
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

            base_dir = os.path.dirname(rel_path)

            # Edge Case 8: Check broken image references
            if known_images is not None:
                _check_broken_image_refs(
                    body, rel_path, source.repo_name, base_dir,
                    known_images, stats['errors'],
                )

            body = rewrite_image_urls(body, source.repo_name, base_dir)

            defaults = {
                'title': metadata.get('title', slug),
                'description': metadata.get('description', ''),
                'content_markdown': body,
                'author': metadata.get('author', ''),
                'tags': metadata.get('tags', []),
                'difficulty': metadata.get('difficulty', ''),
                'source_code_url': metadata.get('source_code_url', ''),
                'demo_url': metadata.get('demo_url', ''),
                'cover_image_url': rewrite_cover_image_url(
                    metadata.get('cover_image', '') or metadata.get('cover_image_url', ''),
                    source, rel_path,
                ),
                'required_level': metadata.get('required_level', 0),
                'published': True,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'content_id': project_content_id,
            }

            date_val = metadata.get('date')
            if date_val:
                if isinstance(date_val, str):
                    defaults['date'] = date_type.fromisoformat(date_val)
                elif isinstance(date_val, date_type):
                    defaults['date'] = date_val
            else:
                defaults['date'] = timezone.now().date()

            # Idempotent lookup: prefer content_id, fall back to slug,
            # then to source_path. Issue #310/#311.
            project = Project.objects.filter(
                content_id=project_content_id,
                source_repo=source.repo_name,
            ).first()
            if project is None:
                project = Project.objects.filter(
                    slug=slug, source_repo=source.repo_name,
                ).first()
            if project is None:
                project = Project.objects.filter(
                    source_repo=source.repo_name, source_path=rel_path,
                ).first()

            if project is None:
                project = Project(slug=slug, **defaults)
                project.save()
                created = True
                changed = True
            else:
                identity_changed = (
                    project.slug != slug
                    or project.source_path != rel_path
                )
                if identity_changed or _defaults_differ(project, defaults):
                    project.slug = slug
                    for k, v in defaults.items():
                        setattr(project, k, v)
                    project.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            # Issue #595: warn (don't block) when the rendered HTML still
            # links to a retired URL prefix (e.g. /event-recordings/...).
            # Only check on a write — unchanged rows already passed this
            # gate during their own sync.
            if changed:
                from content.utils.legacy_urls import detect_legacy_urls
                detect_legacy_urls(
                    project.content_html, rel_path, stats['errors'],
                )

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
                'content_type': 'project',
            })

        except Exception as e:
            fallback_slug = os.path.splitext(filename)[0]
            try:
                failed_slug = metadata.get('slug', fallback_slug)
            except Exception:
                failed_slug = fallback_slug
            failed_slugs.add(failed_slug)
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning('Error syncing project %s: %s', rel_path, e)

    # Soft-delete stale projects, excluding failed slugs
    stale = Project.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)
    for proj in stale:
        stats['items_detail'].append({
            'title': proj.title,
            'slug': proj.slug,
            'action': 'deleted',
            'content_type': 'project',
        })
    deleted_count = stale.count()
    stale.update(published=False, status='pending_review')
    stats['deleted'] += deleted_count


