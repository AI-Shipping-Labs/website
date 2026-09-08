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

