"""Interview question sync dispatcher."""

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

def _dispatch_interview_questions(source, repo_dir, file_list, commit_sha,
                                  stats, known_images=None):
    """Walker dispatch handler: process interview-question markdown files.

    ``file_list`` is the set of repo-relative ``.md`` paths classified by
    ``_classify_repo_files`` as root-level lowercase-kebab-case files
    that don't qualify as articles or projects.
    """
    from content.models import InterviewCategory

    seen_slugs = set()

    for rel_path in file_list:
        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)

        try:
            metadata, body = _parse_markdown_file(filepath)
            slug = os.path.splitext(filename)[0]
            seen_slugs.add(slug)

            defaults = {
                'title': metadata.get('title', slug.replace('-', ' ').title()),
                'description': metadata.get('description', ''),
                'status': metadata.get('status', ''),
                'sections_json': metadata.get('sections', []),
                'body_markdown': body,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
            }

            # Issue #225: only count as 'updated' when content actually changed.
            try:
                obj = InterviewCategory.objects.get(slug=slug)
            except InterviewCategory.DoesNotExist:
                obj = InterviewCategory(slug=slug, **defaults)
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
                'slug': slug,
                'action': action,
                'content_type': 'interview_question',
            })

        except Exception as e:
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning(
                'Error syncing interview question %s: %s', rel_path, e,
            )

    # Delete stale categories from this repo
    stale = InterviewCategory.objects.filter(
        source_repo=source.repo_name,
    ).exclude(slug__in=seen_slugs)
    for cat in stale:
        stats['items_detail'].append({
            'title': cat.title,
            'slug': cat.slug,
            'action': 'deleted',
            'content_type': 'interview_question',
        })
    deleted_count = stale.count()
    stale.delete()
    stats['deleted'] += deleted_count


# ===========================================================================
# Workshop sync (issue #295)
# ===========================================================================
#
# Workshops live under ``YYYY/YYYY-MM-DD-slug/`` folders in the public
# ``AI-Shipping-Labs/workshops-content`` repo. Each folder may contain a
# ``workshop.yaml`` describing the workshop plus a series of numbered
# ``NN-name.md`` pages. Folders without ``workshop.yaml`` are code-only and
# silently skipped.
#
# The pipeline mirrors ``_sync_courses`` in shape (single-entry helper +
# stale cleanup) but is flatter — there is no module layer between a
# Workshop and its WorkshopPage rows.


