"""Download sync parser (family adapter over the moved dispatcher)."""

import os

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import raise_if_checkout_error
from content.sync_parsers.common import logger
from content.sync_parsers.media import rewrite_cover_image_url
from content.sync_parsers.parsing import (
    _check_slug_collision,
    _defaults_differ,
    _parse_yaml_file,
    _validate_frontmatter,
)
from integrations.services.banner_generator.dispatch import enqueue_if_missing as _enqueue_banner_if_missing


def _sync_download(source, rel_path, data, commit_sha, stats,
                   known_images, seen_slugs, failed_slugs):
    """Upsert one download YAML file (moved dispatcher body)."""
    from content.models import Download

    filename = os.path.basename(rel_path)

    try:
        slug = data.get('slug', os.path.splitext(filename)[0])

        # Edge Case 7: Frontmatter validation
        _validate_frontmatter(data, 'download', rel_path)

        from content.services.download_validation import validate_download_metadata
        secure_metadata = validate_download_metadata(
            storage_key=data.get('storage_key', ''),
            file_type=data.get('file_type', 'other'),
            file_size_bytes=data.get('file_size_bytes', 0),
            required_level=data.get('required_level', 0),
            asset_mime_type=data.get('asset_mime_type', ''),
        )
        from content.services.download_delivery import verify_download_object_exists
        verify_download_object_exists(secure_metadata['storage_key'])

        # Require content_id in frontmatter
        download_content_id = data.get('content_id')
        if not download_content_id:
            msg = f'Skipping {rel_path}: missing content_id in frontmatter'
            logger.warning(msg)
            stats['errors'].append({'file': rel_path, 'error': msg})
            return

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
            return

        seen_slugs.add(slug)

        defaults = {
            'title': data.get('title', slug),
            'description': data.get('description', ''),
            'file_url': data.get('file_url', ''),
            **secure_metadata,
            'cover_image_url': rewrite_cover_image_url(
                data.get('cover_image', '') or data.get('cover_image_url', ''),
                source, rel_path,
                known_images=known_images, errors=stats['errors'],
            ),
            'tags': data.get('tags', []),
            'published': True,
            'delivery_blocked_reason': '',
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
            return

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

        # Issue #788: enqueue auto-banner render. Downloads use the
        # ``download`` content_type (the Lambda payload maps it to
        # ``kind: "Resource"`` per the spec); the dispatcher
        # short-circuits when cover_image_url is set.
        _enqueue_banner_if_missing('download', download.pk)

    except Exception as e:
        raise_if_checkout_error(e)
        fallback_slug = os.path.splitext(filename)[0]
        try:
            failed_slug = data.get('slug', fallback_slug)
        except Exception:
            failed_slug = fallback_slug
        failed_slugs.add(failed_slug)
        stats['errors'].append({'file': rel_path, 'error': str(e)})
        # A previously valid row must fail closed when its source becomes
        # invalid or its private object disappears. Preserve metadata for
        # operator diagnosis and permit a later valid sync to recover it.
        Download.objects.filter(
            slug=failed_slug,
            source_repo=source.repo_name,
        ).update(
            published=False,
            delivery_blocked_reason=(
                'Source validation failed; correct the source and re-sync.'
            ),
        )


def _cleanup_downloads(source, stats, seen_slugs, failed_slugs):
    """Soft-delete stale downloads, excluding failed slugs (moved tail)."""
    from content.models import Download
    from content.nav_availability import refresh_published_downloads_nav_cache

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
    refresh_published_downloads_nav_cache()
    return deleted_count


class DownloadsParser(FamilyParser):
    content_type = 'downloads'
    state_name = 'downloads'

    def iter_items(self, run):
        state = self._state(run)
        for rel_path in run.classification().download_files:
            filepath = os.path.join(run.repo_dir, rel_path)
            filename = os.path.basename(rel_path)
            try:
                data = _parse_yaml_file(filepath)
            except Exception as e:  # noqa: BLE001 - bounded per-file capture
                raise_if_checkout_error(e)
                state.record_error({'file': rel_path, 'error': str(e)})
                state.failed.add(os.path.splitext(filename)[0])
                continue
            slug = None
            if isinstance(data, dict):
                slug = data.get(
                    'slug', os.path.splitext(filename)[0],
                )
            yield rel_path, {
                'rel_path': rel_path,
                'data': data,
                'identity': slug or os.path.splitext(filename)[0],
            }

    def process(self, run, payload):
        state = self._state(run)
        stats = self.item_stats()
        _sync_download(
            run.source, payload['rel_path'], payload['data'],
            run.commit_sha, stats, run.known_images(),
            state.seen, state.failed,
        )
        action = self.absorb(run, stats)
        return action, None

    def cleanup(self, run):
        state = self._state(run)
        stats = self.item_stats()
        deleted = _cleanup_downloads(
            run.source, stats, state.seen, state.failed,
        )
        self.absorb(run, stats)
        return deleted
