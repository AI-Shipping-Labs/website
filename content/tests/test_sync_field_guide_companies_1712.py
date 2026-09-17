"""Tests for the sync_field_guide_companies converter command (issue #1712)."""

import tempfile
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import yaml
from django.core.management import call_command
from django.test import TestCase

from content.models import InterviewCompany
from content.services import field_guide_companies as converter
from content.sync_parsers.families.company_interviews import (
    _sync_company_interview,
)

NEARFORM_US = """company: "Nearform"
role: "Senior AI Python Software Engineer"
source_files:
  - "8373891_Nearform_Senior_AI_Python_Software_Engineer_Perm_USA_Rem.yaml"
steps:
  - name: "Talent call"
    duration: "30 min"
    details: "Zoom"
  - name: "Technical assessment - live coding"
    duration: "1 hour"
notable: "Consulting firm; may include client interview."
"""

NEARFORM_UK = """company: "Nearform"
role: "Senior AI Python Software Engineer (UK Perm)"
source_files:
  - "8492964_Nearform_Senior_AI_Python_Software_Engineer_Perm_UK.yaml"
steps:
  - name: "Talent Call"
    duration: "30 min"
notable: "Same structure as US role."
"""

SPEECHIFY = """company: "Speechify"
role: "Multiple roles"
source_files:
  - "1393425_Speechify.yaml"
process_summary: "Several technical interviews, aim to complete within 1 week"
notable: null
"""

EXISTING_NEATFORM = """company: Nearform
content_id: 11111111-1111-1111-1111-111111111111
required_level: 20
roles:
- Senior AI Python Software Engineer
source_files:
- 8373891_Nearform_Senior_AI_Python_Software_Engineer_Perm_USA_Rem.yaml
status: coming-soon
steps: []
"""


class SyncFieldGuideCompaniesTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.guide = self._write_guide(root)
        self.repo = root / 'content-repo'
        self.repo.mkdir(parents=True)

    def _write_guide(self, root):
        postings = root / 'guide' / 'interview' / 'data' / 'job-descriptions'
        postings.mkdir(parents=True)
        sources = {
            '8373891.yaml': NEARFORM_US,
            '8492964.yaml': NEARFORM_UK,
            '1393425.yaml': SPEECHIFY,
        }
        for name, text in sources.items():
            (postings / name).write_text(text, encoding='utf-8')
        return root / 'guide'

    def _target(self, name):
        return self.repo / 'interview-companies' / name

    def _run(self, write=False):
        out = StringIO()
        args = [
            'sync_field_guide_companies',
            '--from-disk', str(self.guide),
            '--content-repo', str(self.repo),
        ]
        if write:
            args.append('--write')
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_dry_run_default_writes_no_files(self):
        output = self._run(write=False)
        self.assertFalse((self.repo / 'interview-companies').exists())
        self.assertIn(
            '1393425.yaml -> interview-companies/speechify.yaml', output,
        )
        self.assertIn(
            '8373891.yaml, 8492964.yaml (2 postings merged) '
            '-> interview-companies/nearform.yaml: 2 roles, 2 steps',
            output,
        )
        self.assertIn('Dry run', output)

    def test_write_emits_one_file_per_company(self):
        self._run(write=True)
        self.assertTrue(self._target('nearform.yaml').exists())
        self.assertTrue(self._target('speechify.yaml').exists())
        # Source posting filenames never become target files.
        self.assertFalse(self._target('8373891.yaml').exists())
        self.assertEqual(
            sorted(p.name for p in (self.repo / 'interview-companies')
                   .iterdir()),
            ['nearform.yaml', 'speechify.yaml'],
        )

    def test_merged_company_unions_roles_sources_and_keeps_first_steps(self):
        self._run(write=True)
        data = yaml.safe_load(
            self._target('nearform.yaml').read_text(encoding='utf-8'))
        self.assertEqual(data['company'], 'Nearform')
        self.assertEqual(data['roles'], [
            'Senior AI Python Software Engineer',
            'Senior AI Python Software Engineer (UK Perm)',
        ])
        self.assertEqual(data['source_files'], [
            '8373891_Nearform_Senior_AI_Python_Software_Engineer_Perm_USA_Rem.yaml',
            '8492964_Nearform_Senior_AI_Python_Software_Engineer_Perm_UK.yaml',
        ])
        # Steps come from the first posting (filename order) that has them;
        # the near-identical UK steps are not appended a second time.
        self.assertEqual(
            [s['name'] for s in data['steps']],
            ['Talent call', 'Technical assessment - live coding'],
        )
        self.assertEqual(data['steps'][0]['duration'], '30 min')
        self.assertEqual(data['steps'][0]['details'], 'Zoom')
        # Distinct notable texts from both postings are preserved.
        self.assertIn('Consulting firm', data['notable'])
        self.assertIn('Same structure as US role.', data['notable'])

    def test_sparse_company_emits_without_steps_or_notable(self):
        self._run(write=True)
        data = yaml.safe_load(
            self._target('speechify.yaml').read_text(encoding='utf-8'))
        self.assertNotIn('steps', data)
        self.assertNotIn('notable', data)
        self.assertEqual(
            data['process_summary'],
            'Several technical interviews, aim to complete within 1 week',
        )
        self.assertEqual(data['roles'], ['Multiple roles'])

    def test_new_files_get_content_id_published_status_basic_level(self):
        self._run(write=True)
        for name in ('nearform.yaml', 'speechify.yaml'):
            data = yaml.safe_load(
                self._target(name).read_text(encoding='utf-8'))
            uuid.UUID(data['content_id'])  # must be a valid UUID
            self.assertEqual(data['status'], 'published')
            self.assertEqual(data['required_level'], 10)

    def test_existing_target_keeps_content_id_status_and_level(self):
        target = self._target('nearform.yaml')
        target.parent.mkdir(parents=True)
        target.write_text(EXISTING_NEATFORM, encoding='utf-8')
        self._run(write=True)
        data = yaml.safe_load(target.read_text(encoding='utf-8'))
        self.assertEqual(
            data['content_id'], '11111111-1111-1111-1111-111111111111',
        )
        self.assertEqual(data['status'], 'coming-soon')
        self.assertEqual(data['required_level'], 20)

    def test_idempotent_second_run_byte_identical(self):
        self._run(write=True)
        target_dir = self.repo / 'interview-companies'
        first = {p.name: p.read_bytes() for p in target_dir.iterdir()}
        self._run(write=True)
        second = {p.name: p.read_bytes() for p in target_dir.iterdir()}
        self.assertEqual(first, second)

    def test_missing_guide_checkout_raises_command_error(self):
        with self.assertRaises(Exception) as ctx:
            call_command(
                'sync_field_guide_companies',
                '--from-disk', str(self.repo),
                '--content-repo', str(self.repo),
            )
        self.assertIn('interview/data/job-descriptions/', str(ctx.exception))

    def test_help_documents_flags_and_refresh_loop(self):
        out = StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(out):
                call_command('sync_field_guide_companies', '--help')
        help_text = out.getvalue()
        self.assertIn('--from-disk', help_text)
        self.assertIn('--content-repo', help_text)
        self.assertIn('--write', help_text)
        self.assertIn('Review the diff in the content repo', help_text)

    def test_parser_round_trip_upserts_company(self):
        self._run(write=True)
        data = yaml.safe_load(
            self._target('nearform.yaml').read_text(encoding='utf-8'))
        stats = {
            'created': 0, 'updated': 0, 'unchanged': 0,
            'deleted': 0, 'errors': [], 'items_detail': [],
        }
        source = SimpleNamespace(repo_name='AI-Shipping-Labs/content')
        _sync_company_interview(
            source, 'interview-companies/nearform.yaml',
            data, 'abc123', stats, set(),
        )
        company = InterviewCompany.objects.get(slug='nearform')
        self.assertEqual(company.company, 'Nearform')
        self.assertEqual(len(company.roles_json), 2)
        self.assertEqual(len(company.steps_json), 2)
        self.assertEqual(len(company.source_files_json), 2)
        # The joined notable survives the YAML round trip verbatim.
        self.assertEqual(company.notable, data['notable'])
        self.assertEqual(company.status, 'published')
        self.assertEqual(company.required_level, 10)
        self.assertEqual(company.source_repo, 'AI-Shipping-Labs/content')


class FieldGuideCompaniesServiceTest(TestCase):
    def _guide_root(self, postings):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        postings_dir = (
            Path(tmp.name) / 'interview' / 'data' / 'job-descriptions'
        )
        postings_dir.mkdir(parents=True)
        for name, text in postings.items():
            (postings_dir / name).write_text(text, encoding='utf-8')
        return tmp.name

    def test_company_slug_follows_1705_slug_rules(self):
        # Avoid Tailwind-token-shaped slugs in assertions: the
        # layout-assertion ratchet reads literals like the one #1705
        # routed around when asserting on section ids.
        self.assertEqual(
            converter.company_slug('Prudential plc (PHI)'),
            'prudential-plc-phi',
        )
        self.assertEqual(
            converter.company_slug('Sprinter Health'),
            'sprinter-health',
        )

    def test_slug_collision_between_distinct_companies_raises(self):
        root = self._guide_root({
            '1.yaml': 'company: "Foo Bar"\nrole: "AI Engineer"\n',
            '2.yaml': 'company: "Foo-bar"\nrole: "AI Engineer"\n',
        })
        with self.assertRaises(ValueError) as ctx:
            converter.company_entries(root)
        self.assertIn('Slug collision', str(ctx.exception))

    def test_missing_company_field_raises(self):
        root = self._guide_root({'1.yaml': 'role: "AI Engineer"\n'})
        with self.assertRaises(ValueError) as ctx:
            converter.company_entries(root)
        self.assertIn('Missing required field "company"', str(ctx.exception))

    def test_empty_companies_dir_raises(self):
        root = self._guide_root({})
        with self.assertRaises(ValueError) as ctx:
            converter.company_entries(root)
        self.assertIn('No company YAML files found', str(ctx.exception))
