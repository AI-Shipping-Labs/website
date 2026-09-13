"""Interview question sync parser (family adapter over the moved dispatcher)."""

import os

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import raise_if_checkout_error
from content.sync_parsers.common import logger
from content.sync_parsers.parsing import (
    _defaults_differ,
    _parse_markdown_file,
)


def _sync_interview_question(source, rel_path, metadata, body, commit_sha,
                             stats, seen_slugs):
    """Upsert one interview-question markdown file (moved dispatcher body)."""
    from content.models import InterviewCategory

    filename = os.path.basename(rel_path)
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
        'content_type': 'interview_question',
    })


def _cleanup_interview_questions(source, stats, seen_slugs):
    """Delete stale categories from this repo (moved dispatcher tail)."""
    from content.models import InterviewCategory

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
    return deleted_count


class InterviewQuestionsParser(FamilyParser):
    content_type = 'interview_questions'
    state_name = 'interview_questions'

    def iter_items(self, run):
        for rel_path in run.classification().interview_files:
            yield rel_path, {'rel_path': rel_path}

    def process(self, run, payload):
        state = self._state(run)
        stats = self.item_stats()
        rel_path = payload['rel_path']
        filepath = os.path.join(run.repo_dir, rel_path)
        try:
            metadata, body = _parse_markdown_file(filepath)
        except Exception as e:  # noqa: BLE001 - bounded per-file capture
            raise_if_checkout_error(e)
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning(
                'Error syncing interview question %s: %s', rel_path, e,
            )
            state.failed.add(os.path.splitext(os.path.basename(rel_path))[0])
            self.absorb(run, stats)
            return 'unchanged', None
        _sync_interview_question(
            run.source, rel_path, metadata, body, run.commit_sha,
            stats, state.seen,
        )
        action = self.absorb(run, stats)
        return action, None

    def cleanup(self, run):
        state = self._state(run)
        stats = self.item_stats()
        deleted = _cleanup_interview_questions(run.source, stats, state.seen)
        self.absorb(run, stats)
        return deleted
