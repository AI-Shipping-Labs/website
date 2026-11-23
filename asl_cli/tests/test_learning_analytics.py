"""Sequential full-history export and faithful CSV output."""

import csv
import io
import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import learning_analytics
from click.testing import CliRunner


def event(number):
    return {'id': number, 'occurred_at': '2026-11-01T12:00:00Z',
            'user': {'email': 'member@test.com', 'name': 'Name, With Comma'},
            'unit': {'title': 'Lesson', 'module_title': 'Module',
                     'optional_module': True, 'is_bonus': True},
            'target_url': '/courses/buildcamp/bonus/lesson'}


class Client:
    def __init__(self, repeat=False):
        self.calls = []
        self.repeat = repeat

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        if len(self.calls) == 1 or self.repeat:
            return {'activities': [event(2)], 'next_cursor': 'page2', 'has_more': True,
                    'total_count': 2, 'measurement': 'lesson_open'}
        return {'activities': [event(1)], 'next_cursor': None, 'has_more': False,
                'total_count': 2, 'measurement': 'lesson_open'}


def test_all_follows_cursor_and_preserves_every_filter(monkeypatch):
    client = Client()
    monkeypatch.setattr(learning_analytics, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, [
        'analytics', 'course-activity', 'buildcamp', '--all', '--cohort', '4',
        '--module', '12', '--unit', '34', '--email', 'member@test.com',
        '--since', '2026-09-21T00:00:00Z', '--until', '2026-11-23T00:00:00Z',
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [row['id'] for row in data['activities']] == [2, 1]
    assert data['has_more'] is False
    assert data['count'] == 2
    assert len(client.calls) == 2
    assert client.calls[0][0] == '/api/courses/buildcamp/learning-activity'
    assert client.calls[1][1] == {**client.calls[0][1], 'cursor': 'page2'}
    assert client.calls[1][1]['cohort'] == '4'
    assert client.calls[1][1]['unit'] == 34


def test_default_returns_one_page(monkeypatch):
    client = Client()
    monkeypatch.setattr(learning_analytics, 'get_client', lambda: client)
    result = CliRunner().invoke(cli, ['analytics', 'course-activity', 'buildcamp'])
    assert result.exit_code == 0, result.output
    assert len(client.calls) == 1
    assert json.loads(result.output)['next_cursor'] == 'page2'


def test_csv_quotes_names_and_includes_all_pages(monkeypatch):
    monkeypatch.setattr(learning_analytics, 'get_client', lambda: Client())
    result = CliRunner().invoke(cli, ['analytics', 'course-activity', 'buildcamp', '--all', '-f', 'csv'])
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert b'\r\r\n' not in result.stdout_bytes
    assert len(rows) == 2
    assert rows[0]['name'] == 'Name, With Comma'
    assert rows[0]['optional_module'] == 'True'


def test_repeated_cursor_fails_loudly(monkeypatch):
    monkeypatch.setattr(learning_analytics, 'get_client', lambda: Client(repeat=True))
    result = CliRunner().invoke(cli, ['analytics', 'course-activity', 'buildcamp', '--all'])
    assert result.exit_code != 0
    assert 'export is incomplete' in result.output


def test_csv_neutralizes_formulas_without_changing_json_values():
    for text in ('=1+1', '+1+1', '-1+1', '@SUM(1)', '  =1+1', '\t=1+1'):
        row = {'name': text, 'id': 7, 'url': '/courses/a/b'}
        safe = learning_analytics._csv_row(row)
        assert safe['name'] == "'" + text
        assert safe['id'] == 7
        assert safe['url'] == '/courses/a/b'
        assert row['name'] == text


@pytest.mark.parametrize('fmt', ['json', 'csv'])
def test_export_uses_utf8_even_with_a_legacy_windows_stdout(monkeypatch, fmt):
    class UnicodeClient:
        def get(self, path, params):
            row = event(1)
            row['user']['name'] = 'Jos\u00e9 \u5f20'
            return {'activities': [row], 'next_cursor': None, 'has_more': False,
                    'total_count': 1}

    monkeypatch.setattr(learning_analytics, 'get_client', lambda: UnicodeClient())
    result = CliRunner(charset='cp1252').invoke(
        cli, ['analytics', 'course-activity', 'buildcamp', '-f', fmt],
    )
    assert result.exit_code == 0, repr(result.exception)
    assert 'Jos\u00e9 \u5f20' in result.stdout_bytes.decode('utf-8')
