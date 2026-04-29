"""Article sync dispatcher."""

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

def _dispatch_articles(source, repo_dir, file_list, commit_sha, stats,
                       known_images=None):
    """Walker dispatch handler: process article markdown files.

    Iterates ``file_list`` (repo-relative paths) and upserts an ``Article``
    row for each. Performs same-source slug-collision warnings and the
    stale-content soft-delete sweep at the end. Mirrors the body of the
    legacy ``_sync_articles`` orchestrator but takes its file list from
    the walker rather than walking the tree itself.
    """
    from content.models import Article

    seen_slugs = set()
    failed_slugs = set()

    for rel_path in file_list:
        filepath = os.path.join(repo_dir, rel_path)
        filename = os.path.basename(rel_path)
        current_slug = None  # Track slug for error handling

        try:
            metadata, body = _parse_markdown_file(filepath)

            # Derive slug before validation so we can track failures
            current_slug = metadata.get('slug', os.path.splitext(filename)[0])

            # Edge Case 7: Frontmatter validation
            _validate_frontmatter(metadata, 'article', rel_path)

            # Require content_id in frontmatter
            content_id = metadata.get('content_id')
            if not content_id:
                msg = f'Skipping {rel_path}: missing content_id in frontmatter'
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                continue

            # Edge Case 2: Check slug collision across sources
            if _check_slug_collision(Article, current_slug, source.repo_name, rel_path):
                stats['errors'].append({
                    'file': rel_path,
                    'error': (
                        f"Slug collision: '{current_slug}' already exists from a "
                        f"different source. Skipped."
                    ),
                })
                failed_slugs.add(current_slug)
                continue

            # Warn on same-source slug collision (last-file-wins)
            if current_slug in seen_slugs:
                logger.warning(
                    'Same-source slug collision: %s appears multiple '
                    'times in %s. Last file wins.',
                    current_slug, source.repo_name,
                )

            seen_slugs.add(current_slug)

            # Rewrite image URLs
            base_dir = os.path.dirname(rel_path)

            # Edge Case 8: Check for broken image references
            if known_images is not None:
                _check_broken_image_refs(
                    body, rel_path, source.repo_name, base_dir,
                    known_images, stats['errors'],
                )

            body = rewrite_image_urls(body, source.repo_name, base_dir)

            # Extract page_type and data from frontmatter
            page_type = metadata.get('page_type', 'blog')
            data = metadata.get('data', {})

            defaults = {
                'title': metadata.get('title', current_slug),
                'description': metadata.get('description', ''),
                'content_markdown': body,
                'author': metadata.get('author', ''),
                'tags': metadata.get('tags', []),
                'cover_image_url': rewrite_cover_image_url(
                    metadata.get('cover_image', '') or metadata.get('cover_image_url', ''),
                    source, rel_path,
                ),
                'required_level': metadata.get('required_level', 0),
                'published': True,
                'source_repo': source.repo_name,
                'source_path': rel_path,
                'source_commit': commit_sha,
                'page_type': page_type,
                'data_json': data,
                'content_id': content_id,
            }

            # Parse date
            date_str = metadata.get('date')
            if date_str:
                from datetime import date as date_type
                if isinstance(date_str, str):
                    defaults['date'] = date_type.fromisoformat(date_str)
                elif isinstance(date_str, date_type):
                    defaults['date'] = date_str
            else:
                defaults['date'] = timezone.now().date()

            # Pre-derive description so the no-change comparison below
            # matches what Article.save() would persist. Article.save()
            # auto-fills description from the first 200 chars of
            # content_markdown when description is empty; if we left
            # defaults['description'] = '' we'd see a spurious diff on
            # every re-sync (issue #225).
            if not defaults['description'] and body:
                defaults['description'] = body[:200]

            # Idempotent lookup: prefer content_id within this source's
            # repo (issue #311 / #310), fall back to slug, then to
            # source_path. This lets authors rename articles without
            # triggering a duplicate insert.
            article = Article.objects.filter(
                content_id=content_id,
                source_repo=source.repo_name,
            ).first()
            if article is None:
                article = Article.objects.filter(
                    slug=current_slug,
                    source_repo=source.repo_name,
                ).first()
            if article is None:
                article = Article.objects.filter(
                    source_repo=source.repo_name,
                    source_path=rel_path,
                ).first()

            if article is None:
                article = Article(
                    slug=current_slug, **defaults,
                )
                article.save()
                created = True
                changed = True
            else:
                identity_changed = (
                    article.slug != current_slug
                    or article.source_path != rel_path
                )
                if identity_changed or _defaults_differ(article, defaults):
                    article.slug = current_slug
                    for k, v in defaults.items():
                        setattr(article, k, v)
                    article.save()
                    created = False
                    changed = True
                else:
                    created = False
                    changed = False

            # Expand widgets after save (save already rendered markdown to HTML)
            # Only re-run when we actually saved — for an unchanged row the
            # rendered HTML is already correct.
            if changed and article.data_json:
                from content.utils.widgets import expand_widgets
                expanded = expand_widgets(article.content_html, article.data_json)
                Article.objects.filter(pk=article.pk).update(content_html=expanded)

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
                'slug': current_slug,
                'action': action,
                'content_type': 'article',
            })

        except Exception as e:
            # Track the slug as failed so it's excluded from cleanup.
            # Use filename-based slug as safe fallback, plus current_slug
            # if it was derived from metadata before the error.
            failed_slugs.add(os.path.splitext(filename)[0])
            if current_slug:
                failed_slugs.add(current_slug)
            stats['errors'].append({
                'file': rel_path,
                'error': str(e),
            })
            logger.warning('Error syncing article %s: %s', rel_path, e)

    # Edge Case 3: Exclude failed_slugs from stale-content cleanup
    stale_articles = Article.objects.filter(
        source_repo=source.repo_name,
        published=True,
    ).exclude(slug__in=seen_slugs).exclude(slug__in=failed_slugs)

    for article in stale_articles:
        stats['items_detail'].append({
            'title': article.title,
            'slug': article.slug,
            'action': 'deleted',
            'content_type': 'article',
        })
    deleted_count = stale_articles.count()
    stale_articles.update(published=False, status='draft')
    stats['deleted'] += deleted_count


