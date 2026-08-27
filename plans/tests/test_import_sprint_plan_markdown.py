import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts import import_sprint_plan_markdown as importer
from scripts.import_sprint_plan_markdown import parse_resources

MARKDOWN_PLAN = """## Summary

- Current situation: Building an eval harness
- Goal for the next 6 weeks: Ship the toolkit
- Main gap to close: No deployment story
- Weekly time commitment: 8 hours
- Why this plan is the right next step: It unblocks the launch

## Focus

- Main focus: Evaluation toolkit
- Supporting focus: Deployment

## Timeline

Week 1:

- Set up the harness

## Resources

- [Docs](https://docs.example.com)

## Deliverables

- A working harness

## Accountability

Weekly demo.

## Next Steps

- Book the first session
"""


class FakeApiClient:
    """Records every importer API call so tests can assert side effects."""

    def __init__(self, *, ready_status='sent', plan_id=116):
        self.calls = []
        self.ready_status = ready_status
        self.plan_id = plan_id

    def request(self, path, *, method='GET', payload=None):
        self.calls.append((method, path, payload))
        if method == 'GET' and path.endswith('/plans'):
            return 200, {'plans': [{
                'id': self.plan_id,
                'user_email': 'member@example.com',
            }]}
        if method == 'GET' and path.startswith('/plans/'):
            return 200, {'id': self.plan_id, 'weeks': []}
        if method == 'PATCH':
            return 200, {
                'id': self.plan_id,
                'user_email': 'member@example.com',
                'sprint': 'august-2026',
                'shared_at': None,
                'weeks': [],
            }
        if method == 'POST' and path.endswith('/send-ready-email'):
            return 200, {
                'plan_id': self.plan_id,
                'member_id': 42,
                'member_email': 'member@example.com',
                'sprint_slug': 'august-2026',
                'shared_at': (
                    '2026-08-14T12:56:44+00:00'
                    if self.ready_status in ('sent', 'already_sent')
                    else None
                ),
                'ready_email': {
                    'dry_run': False,
                    'status': self.ready_status,
                    'eligible': False,
                    'requested': True,
                    'sent': self.ready_status == 'sent',
                    'skipped_already_sent': (
                        self.ready_status == 'already_sent'
                    ),
                    'skipped_already_shared': (
                        self.ready_status == 'already_shared'
                    ),
                    'failed': self.ready_status == 'failed_retryable',
                    'retryable': self.ready_status == 'failed_retryable',
                    'sent_at': None,
                    'error': (
                        'ses exploded'
                        if self.ready_status == 'failed_retryable'
                        else ''
                    ),
                },
            }
        raise AssertionError(f'Unexpected call: {method} {path}')


class SprintPlanMarkdownDeliveryIntentTest(SimpleTestCase):
    """The importer must always choose an explicit delivery intent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / 'plan.md'
        self.source.write_text(MARKDOWN_PLAN, encoding='utf-8')

    def _run(self, extra_args, *, client=None):
        client = client or FakeApiClient()
        argv = [
            '--sprint', 'august-2026',
            '--email', 'member@example.com',
            '--source', str(self.source),
            '--env-file', str(Path(self._tmp.name) / 'missing.env'),
            *extra_args,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(
            'os.environ',
            {'API_SHIPPING_LABS_API_TOKEN': 'test-token'},
        ), patch.object(importer, 'ApiClient', return_value=client), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = importer.main(argv)
        return code, stdout.getvalue(), client

    def test_missing_intent_is_rejected_before_any_call(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run([])
        self.assertEqual(ctx.exception.code, 2)

    def test_both_intents_are_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(['--send-ready-email', '--no-ready-email'])
        self.assertEqual(ctx.exception.code, 2)

    def test_dry_run_reports_intent_and_performs_no_writes(self):
        code, output, client = self._run(['--send-ready-email', '--dry-run'])

        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report['ready_email_intent'], 'send-ready-email')
        self.assertFalse(report['ready_email']['requested'])
        methods = {method for method, _path, _payload in client.calls}
        self.assertEqual(methods, {'GET'})

    def test_send_ready_email_runs_after_a_successful_patch(self):
        code, output, client = self._run(['--send-ready-email'])

        self.assertEqual(code, 0)
        ordered = [
            (method, path) for method, path, _payload in client.calls
            if method in {'PATCH', 'POST'}
        ]
        self.assertEqual(ordered, [
            ('PATCH', '/plans/116'),
            ('POST', '/plans/116/send-ready-email'),
        ])
        report = json.loads(output)
        self.assertEqual(report['ready_email']['status'], 'sent')
        self.assertEqual(report['shared_at'], '2026-08-14T12:56:44+00:00')

    def test_failed_ready_email_report_does_not_expose_provider_details(self):
        client = FakeApiClient(ready_status='failed_retryable')

        code, output, _client = self._run(
            ['--send-ready-email'], client=client,
        )

        self.assertEqual(code, 4)
        self.assertNotIn('ses exploded', output)
        self.assertEqual(
            json.loads(output)['ready_email']['error'],
            importer.READY_EMAIL_PUBLIC_ERROR,
        )

    def test_no_ready_email_never_calls_the_ready_endpoint(self):
        code, output, client = self._run(['--no-ready-email'])

        self.assertEqual(code, 0)
        self.assertFalse(any(
            path.endswith('/send-ready-email')
            for _method, path, _payload in client.calls
        ))
        report = json.loads(output)
        self.assertEqual(report['ready_email_intent'], 'no-ready-email')
        self.assertEqual(report['ready_email']['status'], 'not_requested')
        self.assertFalse(report['ready_email']['requested'])

    def test_already_sent_is_a_safe_terminal_outcome(self):
        code, output, _client = self._run(
            ['--send-ready-email'],
            client=FakeApiClient(ready_status='already_sent'),
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output)['ready_email']['status'], 'already_sent',
        )

    def test_failed_delivery_exits_non_zero(self):
        code, output, _client = self._run(
            ['--send-ready-email'],
            client=FakeApiClient(ready_status='failed_retryable'),
        )

        self.assertNotEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report['ready_email']['status'], 'failed_retryable')
        self.assertTrue(report['ready_email']['retryable'])

    def test_report_never_prints_tokens_or_auth_headers(self):
        _code, output, _client = self._run(['--send-ready-email'])

        self.assertNotIn('test-token', output)
        self.assertNotIn('Authorization', output)


class SprintPlanMarkdownResourceImportTest(SimpleTestCase):
    def test_parse_resources_builds_structured_rows(self):
        body = "\n".join([
            "- [Buildcamp](https://buildcamp.example.com)",
            "- Deployment - [Docs](https://docs.example.com/deploy)",
            "- Logfire - note with [Docs](https://logfire.pydantic.dev/)",
            "- amr_ai repo - https://github.com/juanpprim/amr_ai",
            "- Carlos's own project notes",
        ])

        resources = parse_resources(body)

        self.assertEqual(
            resources,
            [
                {
                    "title": "Buildcamp",
                    "url": "https://buildcamp.example.com",
                    "note": "",
                    "position": 0,
                },
                {
                    "title": "Deployment",
                    "url": "https://docs.example.com/deploy",
                    "note": "",
                    "position": 1,
                },
                {
                    "title": "Logfire",
                    "url": "https://logfire.pydantic.dev/",
                    "note": "note with [Docs](https://logfire.pydantic.dev/)",
                    "position": 2,
                },
                {
                    "title": "amr_ai repo",
                    "url": "https://github.com/juanpprim/amr_ai",
                    "note": "",
                    "position": 3,
                },
                {
                    "title": "Carlos's own project notes",
                    "url": "",
                    "note": "",
                    "position": 4,
                },
            ],
        )

    def test_parse_resources_keeps_multiline_note_text(self):
        resources = parse_resources(
            "- Deployment - note line one\n"
            "  continued note with [Docs](https://docs.example.com/deploy)"
        )

        self.assertEqual(resources[0]["title"], "Deployment")
        self.assertEqual(resources[0]["url"], "https://docs.example.com/deploy")
        self.assertIn("continued note", resources[0]["note"])
