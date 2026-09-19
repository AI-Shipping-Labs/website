"""Company interview sync parser (family adapter over the moved dispatcher)."""

import os

from content.sync_parsers.base import FamilyParser
from content.sync_parsers.checkout_view import raise_if_checkout_error
from content.sync_parsers.common import logger
from content.sync_parsers.parsing import _defaults_differ, _parse_yaml_file


def _sync_company_interview(source, rel_path, data, commit_sha, stats,
                            seen_slugs):
    """Upsert one company interview YAML file (issue #1712)."""
    from content.access import LEVEL_BASIC
    from content.models import InterviewCompany

    filename = os.path.basename(rel_path)
    slug = os.path.splitext(filename)[0]
    company = data.get('company')
    if not company:
        stats['errors'].append({
            'file': rel_path,
            'error': 'Missing required field "company".',
        })
        return
    seen_slugs.add(slug)

    required_level = data.get('required_level')
    if required_level is None:
        required_level = LEVEL_BASIC

    defaults = {
        'company': company,
        'roles_json': data.get('roles', []),
        'process_summary': data.get('process_summary', ''),
        'steps_json': data.get('steps', []),
        'notable': data.get('notable', '') or '',
        'source_files_json': data.get('source_files', []),
        'status': data.get('status', ''),
        'required_level': required_level,
        'source_repo': source.repo_name,
        'source_path': rel_path,
        'source_commit': commit_sha,
    }

    # Issue #225: only count as 'updated' when content actually changed.
    try:
        obj = InterviewCompany.objects.get(slug=slug)
    except InterviewCompany.DoesNotExist:
        obj = InterviewCompany(slug=slug, **defaults)
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
        'title': company,
        'slug': slug,
        'action': action,
        'content_type': 'company_interview',
    })


def _cleanup_company_interviews(source, stats, seen_slugs):
    """Delete stale company rows from this repo (moved dispatcher tail)."""
    from content.models import InterviewCompany

    stale = InterviewCompany.objects.filter(
        source_repo=source.repo_name,
    ).exclude(slug__in=seen_slugs)
    for company in stale:
        stats['items_detail'].append({
            'title': company.company,
            'slug': company.slug,
            'action': 'deleted',
            'content_type': 'company_interview',
        })
    deleted_count = stale.count()
    stale.delete()
    stats['deleted'] += deleted_count
    return deleted_count


class CompanyInterviewsParser(FamilyParser):
    content_type = 'company_interviews'
    state_name = 'company_interviews'

    def iter_items(self, run):
        for rel_path in run.classification().company_interview_files:
            yield rel_path, {'rel_path': rel_path}

    def process(self, run, payload):
        state = self._state(run)
        stats = self.item_stats()
        rel_path = payload['rel_path']
        filepath = os.path.join(run.repo_dir, rel_path)
        try:
            data = _parse_yaml_file(filepath)
        except Exception as e:  # noqa: BLE001 - bounded per-file capture
            raise_if_checkout_error(e)
            stats['errors'].append({'file': rel_path, 'error': str(e)})
            logger.warning(
                'Error syncing company interview %s: %s', rel_path, e,
            )
            state.failed.add(
                os.path.splitext(os.path.basename(rel_path))[0]
            )
            self.absorb(run, stats)
            return 'unchanged', None
        _sync_company_interview(
            run.source, rel_path, data, run.commit_sha,
            stats, state.seen,
        )
        action = self.absorb(run, stats)
        return action, None

    def cleanup(self, run):
        state = self._state(run)
        stats = self.item_stats()
        deleted = _cleanup_company_interviews(run.source, stats, state.seen)
        self.absorb(run, stats)
        return deleted
