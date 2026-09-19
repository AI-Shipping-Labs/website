"""Direct tests for the CompanyInterviewsParser sync family (issue #1712).

Follows the ``_sync_interview_question`` round-trip pattern from #1705:
the moved dispatcher bodies are exercised with a plain stats dict and a
``SimpleNamespace`` source, so upsert/change-detection/stale-cleanup are
covered without a live GitHub checkout.
"""

from types import SimpleNamespace

from django.test import TestCase

from content.models import InterviewCompany
from content.sync_parsers.families.company_interviews import (
    _cleanup_company_interviews,
    _sync_company_interview,
)

SOURCE = SimpleNamespace(repo_name='AI-Shipping-Labs/content')

COMPANY_YAML = {
    'company': 'Nearform',
    'content_id': '11111111-1111-1111-1111-111111111111',
    'roles': ['Senior AI Python Software Engineer'],
    'process_summary': '',
    'steps': [
        {'name': 'Talent call', 'duration': '30 min', 'details': 'Zoom'},
        {'name': 'Technical assessment - live coding', 'duration': '1 hour'},
    ],
    'notable': 'Consulting firm; may include client interview.',
    'source_files': [
        '8373891_Nearform_Senior_AI_Python_Software_Engineer_Perm_USA_Rem.yaml',
    ],
    'status': 'published',
    'required_level': 10,
}


def _stats():
    return {
        'created': 0, 'updated': 0, 'unchanged': 0,
        'deleted': 0, 'errors': [], 'items_detail': [],
    }


def _sync(data, rel_path='interview-companies/nearform.yaml',
          seen=None, stats=None, source=SOURCE):
    stats = stats if stats is not None else _stats()
    seen = seen if seen is not None else set()
    _sync_company_interview(
        source, rel_path, data, 'abc123', stats, seen,
    )
    return stats, seen


class SyncCompanyInterviewUpsertTest(TestCase):
    def test_first_sync_creates_row_with_all_fields(self):
        stats, seen = _sync(COMPANY_YAML)
        self.assertEqual(stats['created'], 1)
        self.assertEqual(seen, {'nearform'})
        company = InterviewCompany.objects.get(slug='nearform')
        self.assertEqual(company.company, 'Nearform')
        self.assertEqual(company.roles_json, ['Senior AI Python Software Engineer'])
        self.assertEqual(len(company.steps_json), 2)
        self.assertEqual(
            company.source_files_json,
            COMPANY_YAML['source_files'],
        )
        self.assertEqual(company.notable, COMPANY_YAML['notable'])
        self.assertEqual(company.status, 'published')
        self.assertEqual(company.required_level, 10)
        self.assertEqual(company.source_repo, 'AI-Shipping-Labs/content')
        self.assertEqual(
            company.source_path, 'interview-companies/nearform.yaml',
        )
        self.assertEqual(company.source_commit, 'abc123')

    def test_unchanged_second_run_reports_unchanged(self):
        _sync(COMPANY_YAML)
        stats, _seen = _sync(COMPANY_YAML)
        self.assertEqual(stats['unchanged'], 1)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(stats['created'], 0)
        self.assertEqual(stats['items_detail'], [])
        self.assertEqual(InterviewCompany.objects.count(), 1)

    def test_changed_content_reports_updated(self):
        _sync(COMPANY_YAML)
        changed = dict(COMPANY_YAML, steps=[{'name': 'New step'}])
        stats, _seen = _sync(changed)
        self.assertEqual(stats['updated'], 1)
        company = InterviewCompany.objects.get(slug='nearform')
        self.assertEqual(company.steps_json, [{'name': 'New step'}])

    def test_sparse_data_defaults(self):
        stats, _seen = _sync({
            'company': 'Speechify',
            'roles': ['Multiple roles'],
        }, rel_path='interview-companies/speechify.yaml')
        self.assertEqual(stats['created'], 1)
        company = InterviewCompany.objects.get(slug='speechify')
        self.assertEqual(company.steps_json, [])
        self.assertEqual(company.process_summary, '')
        self.assertEqual(company.notable, '')
        self.assertEqual(company.source_files_json, [])
        self.assertEqual(company.status, '')
        # required_level defaults to LEVEL_BASIC when frontmatter omits it.
        self.assertEqual(company.required_level, 10)

    def test_coming_soon_status_is_stored_not_dropped(self):
        _sync(dict(COMPANY_YAML, status='coming-soon'))
        company = InterviewCompany.objects.get(slug='nearform')
        self.assertEqual(company.status, 'coming-soon')

    def test_missing_company_is_a_bounded_error(self):
        stats, seen = _sync({'roles': ['AI Engineer']})
        self.assertEqual(stats['errors'], [
            {'file': 'interview-companies/nearform.yaml',
             'error': 'Missing required field "company".'},
        ])
        self.assertEqual(seen, set())
        self.assertEqual(InterviewCompany.objects.count(), 0)


class SyncCompanyInterviewCleanupTest(TestCase):
    def test_deleting_source_file_deletes_row_scoped_by_source_repo(self):
        _sync(COMPANY_YAML)
        InterviewCompany.objects.create(
            slug='elsewhere', company='Elsewhere',
            source_repo='Some_one/else',
        )
        stats = _stats()
        deleted = _cleanup_company_interviews(SOURCE, stats, seen_slugs=set())
        self.assertEqual(deleted, 1)
        self.assertEqual(stats['deleted'], 1)
        self.assertFalse(
            InterviewCompany.objects.filter(slug='nearform').exists(),
        )
        # Rows from other repos are never touched by this repo's cleanup.
        self.assertTrue(
            InterviewCompany.objects.filter(slug='elsewhere').exists(),
        )

    def test_seen_slugs_survive_cleanup(self):
        _sync(COMPANY_YAML)
        stats = _stats()
        deleted = _cleanup_company_interviews(
            SOURCE, stats, seen_slugs={'nearform'},
        )
        self.assertEqual(deleted, 0)
        self.assertEqual(InterviewCompany.objects.count(), 1)
        self.assertEqual(
            [item['action'] for item in stats['items_detail']],
            [],
        )
