"""Marketing page sync parser (family adapter over the moved dispatcher)."""

import os

from django.db import transaction

from content.models import MarketingPage
from content.models.marketing_page import (
    NAV_SECTION_ABOUT,
    NAV_SECTION_COMMUNITY,
    NAV_SECTION_NONE,
    NAV_SECTION_RESOURCES,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    normalize_marketing_page_public_path,
)
from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import raise_if_checkout_error
from content.sync_parsers.media import (
    _check_broken_image_refs,
    rewrite_cover_image_url,
    rewrite_image_urls,
)
from content.sync_parsers.parsing import (
    _defaults_differ,
    _parse_markdown_file,
    _validate_frontmatter,
)

_VALID_STATUSES = {STATUS_DRAFT, STATUS_PUBLISHED}
_VALID_NAV_SECTIONS = {
    NAV_SECTION_NONE,
    NAV_SECTION_ABOUT,
    NAV_SECTION_COMMUNITY,
    NAV_SECTION_RESOURCES,
}


def _coerce_status(metadata, rel_path):
    raw = metadata.get('status') or STATUS_DRAFT
    status = str(raw).strip().lower()
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Unsupported marketing page status in {rel_path}: {raw!r}. "
            "Allowed values are 'draft' or 'published'."
        )
    return status


def _coerce_nav_section(metadata, rel_path):
    raw = metadata.get('nav_section') or NAV_SECTION_NONE
    section = str(raw).strip().lower()
    if section not in _VALID_NAV_SECTIONS:
        raise ValueError(
            f"Unsupported marketing page nav_section in {rel_path}: {raw!r}. "
            "Allowed values are 'none', 'about', 'community', or 'resources'."
        )
    return section


def _coerce_bool(value, *, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes', 'on'}:
            return True
        if lowered in {'false', '0', 'no', 'off'}:
            return False
    return bool(value)


def _coerce_int(value, *, default=0, field_name='value'):
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be an integer.') from exc


def _marketing_page_detail(page, action):
    return {
        'title': page.title,
        'slug': page.public_path,
        'action': action,
        'content_type': 'marketing_page',
    }


def _lookup_marketing_page(source, content_id, public_path, rel_path):
    page = MarketingPage.objects.filter(
        content_id=content_id,
        source_repo=source.repo_name,
    ).first()
    if page is not None:
        return page
    page = MarketingPage.objects.filter(
        public_path=public_path,
        source_repo=source.repo_name,
    ).first()
    if page is not None:
        return page
    return MarketingPage.objects.filter(
        source_repo=source.repo_name,
        source_path=rel_path,
    ).first()


def _path_collision_exists(public_path, source, existing=None):
    qs = MarketingPage.objects.filter(public_path=public_path).exclude(
        source_repo=source.repo_name,
    )
    if existing and existing.pk:
        qs = qs.exclude(pk=existing.pk)
    return qs.exists()


def _sync_marketing_page(
    source,
    repo_dir,
    rel_path,
    metadata,
    body,
    commit_sha,
    stats,
    known_images,
    seen_content_ids,
    failed_content_ids,
    failed_source_paths,
):
    """Upsert one marketing page markdown file (moved dispatcher body)."""
    filepath = os.path.join(repo_dir, rel_path)
    base_dir = os.path.dirname(rel_path)
    content_id = metadata.get('content_id')
    _validate_frontmatter(metadata, 'marketing_page', rel_path)

    public_path = normalize_marketing_page_public_path(
        metadata.get('public_path'),
    )
    if _path_collision_exists(public_path, source):
        stats['errors'].append({
            'file': rel_path,
            'error': (
                f"Public path collision: '{public_path}' already "
                "exists from a different source. Skipped."
            ),
        })
        failed_content_ids.add(content_id)
        failed_source_paths.add(rel_path)
        return

    if known_images is not None:
        _check_broken_image_refs(
            body, rel_path, source.repo_name, base_dir,
            known_images, stats['errors'],
        )
    body = rewrite_image_urls(body, source.repo_name, base_dir)

    defaults = {
        'content_id': content_id,
        'title': metadata.get('title', public_path),
        'public_path': public_path,
        'description': metadata.get('description', ''),
        'meta_description': metadata.get('meta_description', ''),
        'content_markdown': body,
        'cover_image_url': rewrite_cover_image_url(
            metadata.get('cover_image', '') or metadata.get('cover_image_url', ''),
            source, rel_path,
            known_images=known_images, errors=stats['errors'],
        ),
        'tags': metadata.get('tags', []),
        'status': _coerce_status(metadata, rel_path),
        'show_in_sitemap': _coerce_bool(
            metadata.get('show_in_sitemap'),
            default=True,
        ),
        'nav_section': _coerce_nav_section(metadata, rel_path),
        'nav_label': metadata.get('nav_label', ''),
        'nav_order': _coerce_int(
            metadata.get('nav_order'),
            default=0,
            field_name='nav_order',
        ),
        'source_repo': source.repo_name,
        'source_path': rel_path,
        'source_commit': commit_sha,
    }

    with transaction.atomic():
        page = _lookup_marketing_page(
            source, content_id, public_path, rel_path,
        )
        if _path_collision_exists(public_path, source, existing=page):
            stats['errors'].append({
                'file': rel_path,
                'error': (
                    f"Public path collision: '{public_path}' already "
                    "exists from a different source. Skipped."
                ),
            })
            failed_content_ids.add(content_id)
            failed_source_paths.add(rel_path)
            return

        if page is None:
            page = MarketingPage(**defaults)
            page.save()
            created = True
            changed = True
        else:
            identity_changed = (
                page.public_path != public_path
                or page.source_path != rel_path
            )
            if identity_changed or _defaults_differ(page, defaults):
                for key, value in defaults.items():
                    setattr(page, key, value)
                page.save()
                created = False
                changed = True
            else:
                created = False
                changed = False

        if changed and '<!-- include:' in page.content_html:
            from content.utils.includes import expand_content_includes

            expanded = expand_content_includes(
                page.content_html,
                repo_dir=repo_dir,
                base_dir=os.path.dirname(filepath),
                context={'data': metadata},
            )
            MarketingPage.objects.filter(pk=page.pk).update(
                content_html=expanded,
            )
            page.content_html = expanded

    seen_content_ids.add(str(content_id))
    if not changed:
        stats['unchanged'] += 1
        return
    if created:
        stats['created'] += 1
        action = 'created'
    else:
        stats['updated'] += 1
        action = 'updated'
    stats['items_detail'].append(_marketing_page_detail(page, action))


def _cleanup_marketing_pages(source, stats, seen_content_ids,
                             failed_content_ids, failed_source_paths):
    """Soft-delete (draft) marketing pages removed from this source."""
    stale = MarketingPage.objects.filter(
        source_repo=source.repo_name,
    ).exclude(status=STATUS_DRAFT)
    if seen_content_ids:
        stale = stale.exclude(content_id__in=seen_content_ids)
    if failed_content_ids:
        stale = stale.exclude(content_id__in=failed_content_ids)
    if failed_source_paths:
        stale = stale.exclude(source_path__in=failed_source_paths)

    stale_pages = list(stale)
    for page in stale_pages:
        stats['items_detail'].append(_marketing_page_detail(page, 'deleted'))
    if stale_pages:
        stale.update(status=STATUS_DRAFT)
        stats['deleted'] += len(stale_pages)
    return len(stale_pages)


class MarketingPagesParser(FamilyParser):
    content_type = 'marketing_pages'
    state_name = 'marketing_pages'

    def _family_state(self, run):
        state = self._state(run)
        # Marketing pages track content ids and source paths separately.
        if not hasattr(state, 'failed_content_ids'):
            state.failed_content_ids = set()
            state.failed_source_paths = set()
        return state

    def iter_items(self, run):
        state = self._family_state(run)
        for rel_path in run.classification().marketing_page_files:
            filepath = os.path.join(run.repo_dir, rel_path)
            content_id = None
            try:
                metadata, body = _parse_markdown_file(filepath)
                content_id = metadata.get('content_id')
                _validate_frontmatter(metadata, 'marketing_page', rel_path)
            except Exception as exc:  # noqa: BLE001 - bounded per-file capture
                raise_if_checkout_error(exc)
                if content_id:
                    state.failed_content_ids.add(str(content_id))
                state.failed_source_paths.add(rel_path)
                state.record_error({'file': rel_path, 'error': str(exc)})
                continue
            yield rel_path, {
                'rel_path': rel_path,
                'metadata': metadata,
                'body': body,
                'identity': str(content_id) if content_id else None,
            }

    def process(self, run, payload):
        state = self._family_state(run)
        stats = self.item_stats()
        try:
            _sync_marketing_page(
                run.source, run.repo_dir, payload['rel_path'],
                payload['metadata'], payload['body'], run.commit_sha,
                stats, run.known_images(), state.seen,
                state.failed_content_ids, state.failed_source_paths,
            )
        except Exception as exc:  # noqa: BLE001 - bounded per-file capture
            raise_if_checkout_error(exc)
            content_id = payload.get('identity')
            if content_id:
                state.failed_content_ids.add(str(content_id))
            state.failed_source_paths.add(payload['rel_path'])
            stats['errors'].append({'file': payload['rel_path'], 'error': str(exc)})
        action = self.absorb(run, stats)
        return action, None

    def cleanup(self, run):
        state = self._family_state(run)
        stats = self.item_stats()
        deleted = _cleanup_marketing_pages(
            run.source, stats, state.seen,
            state.failed_content_ids, state.failed_source_paths,
        )
        self.absorb(run, stats)
        return deleted
