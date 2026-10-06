"""Offline CLI contracts for ``asl comments``."""

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import comments as comments_module
from click.testing import CliRunner


class RecordingClient:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {'comments': [], 'count': 0, 'limit': 50, 'offset': 0}

    def get(self, path, **kwargs):
        self.calls.append(('GET', path, kwargs))
        return self.result

    def post(self, path, **kwargs):
        self.calls.append(('POST', path, kwargs))
        return self.result


def test_list_maps_filters_and_preserves_json(monkeypatch):
    payload = {
        'comments': [{'id': 1, 'body': 'x' * 80}],
        'count': 1,
        'limit': 100,
        'offset': 3,
    }
    client = RecordingClient(payload)
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, [
        'comments', 'list',
        '--content-type', 'course_unit',
        '--content-id', '00000000-0000-0000-0000-000000000000',
        '--kind', 'top_level',
        '--author-email', 'reader@test.com',
        '--since', '2026-09-01T00:00:00Z',
        '--until', '2026-10-01T00:00:00Z',
        '--unanswered',
        '--course', 'aihero', '--module', 'day-1', '--unit', 'frontmatter',
        '--limit', '100', '--offset', '3', '--format', 'json',
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [('GET', '/api/comments', {'params': {
        'content_type': 'course_unit',
        'content_id': '00000000-0000-0000-0000-000000000000',
        'kind': 'top_level',
        'author_email': 'reader@test.com',
        'since': '2026-09-01T00:00:00Z',
        'until': '2026-10-01T00:00:00Z',
        'unanswered': 'true',
        'course_slug': 'aihero',
        'module_slug': 'day-1',
        'unit_slug': 'frontmatter',
        'limit': 100,
        'offset': 3,
    }})]
    assert json.loads(result.output) == payload


def test_homework_step_filter_passes_through_and_requires_nesting(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, [
        'comments', 'list',
        '--course', 'ai-buildcamp', '--module', 'foundation',
        '--unit', 'homework', '--homework-step', 'q2-reflect',
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [('GET', '/api/comments', {'params': {
        'course_slug': 'ai-buildcamp',
        'module_slug': 'foundation',
        'unit_slug': 'homework',
        'homework_step': 'q2-reflect',
        'limit': 50,
        'offset': 0,
    }})]

    client = RecordingClient()
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, [
        'comments', 'list', '--homework-step', 'q2-reflect',
    ])
    assert result.exit_code == 2, result.output
    assert '--course, --module, and --unit' in result.output
    assert client.calls == []


def test_table_has_stable_columns_and_only_table_truncates(monkeypatch):
    payload = {
        'comments': [{
            'id': 1, 'kind': 'top_level', 'content_type': 'course_unit',
            'author': {'email': 'reader@test.com'}, 'created_at': '2026-09-08T10:00:00Z',
            'reply_count': 0, 'context': {'unit_slug': 'frontmatter'},
            'body': 'x' * 80,
        }],
        'count': 1, 'limit': 50, 'offset': 0,
    }
    client = RecordingClient(payload)
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, ['comments', 'list', '--format', 'table'])
    assert result.exit_code == 0, result.output
    header = result.output.splitlines()[0]
    for column in comments_module.TABLE_COLUMNS:
        assert column in header
    assert '...' in result.output


def test_reply_sends_one_post_with_body_file_and_key(monkeypatch, tmp_path):
    client = RecordingClient({'id': 7, 'body': 'Answer', 'idempotent_replay': False})
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    body_file = tmp_path / 'answer.txt'
    body_file.write_text('  Answer\n', encoding='utf-8')
    result = CliRunner().invoke(cli, [
        'comments', 'reply', '412', '--body-file', str(body_file),
        '--idempotency-key', 'run-412', '--format', 'raw',
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [('POST', '/api/comments/412/replies', {
        'json_body': {'body': 'Answer'},
        'headers': {'Idempotency-Key': 'run-412'},
    })]
    assert json.loads(result.output)['id'] == 7


def test_edit_maps_to_the_moderation_route_without_idempotency_key(monkeypatch, tmp_path):
    """Issue #1894: `asl comments edit` posts {"body": ...} to /edit."""
    client = RecordingClient({'id': 412, 'body': 'Safe question', 'moderation_state': 'visible'})
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    body_file = tmp_path / 'rewrite.txt'
    body_file.write_text('  Safe question\n', encoding='utf-8')
    result = CliRunner().invoke(cli, [
        'comments', 'edit', '412', '--body-file', str(body_file), '--format', 'json',
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [('POST', '/api/comments/412/edit', {
        'json_body': {'body': 'Safe question'},
    })]
    assert json.loads(result.output)['moderation_state'] == 'visible'


def test_hide_and_restore_post_bare_idempotent_moderation_routes(monkeypatch):
    """Issue #1894: hide/restore are bare POSTs with no Idempotency-Key."""
    client = RecordingClient({'id': 412, 'moderation_state': 'hidden'})
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, ['comments', 'hide', '412', '--format', 'json'])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(cli, ['comments', 'restore', '412', '--format', 'json'])
    assert result.exit_code == 0, result.output
    assert client.calls == [
        ('POST', '/api/comments/412/hide', {}),
        ('POST', '/api/comments/412/restore', {}),
    ]


def test_moderation_state_filter_passes_through_to_list(monkeypatch):
    """Issue #1894: `--moderation-state` narrows the operator listing."""
    client = RecordingClient()
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, [
        'comments', 'list', '--moderation-state', 'hidden', '--limit', '100',
    ])
    assert result.exit_code == 0, result.output
    assert client.calls == [('GET', '/api/comments', {'params': {
        'moderation_state': 'hidden',
        'limit': 100,
        'offset': 0,
    }})]

    client = RecordingClient()
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, ['comments', 'list', '--moderation-state', 'archived'])
    assert result.exit_code == 2, result.output
    assert client.calls == []


@pytest.mark.parametrize('arguments', [
    ['comments', 'reply', '1', '--idempotency-key', 'key'],
    ['comments', 'reply', '1', '--body', 'x', '--body-file', 'answer.txt', '--idempotency-key', 'key'],
    ['comments', 'reply', '1', '--body', ' ', '--idempotency-key', 'key'],
    ['comments', 'reply', '1', '--body', 'x'],
    ['comments', 'reply', '1', '--body', 'x', '--idempotency-key', 'bad key'],
    ['comments', 'list', '--module', 'day-1'],
    ['comments', 'list', '--course', 'aihero', '--workshop', 'agents'],
    ['comments', 'list', '--limit', '0'],
])
def test_unsafe_or_ambiguous_input_makes_no_request(monkeypatch, arguments):
    client = RecordingClient()
    monkeypatch.setattr(comments_module, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 2, result.output
    assert client.calls == []
