"""Retrieve every retained learning event with sequential cursor requests."""

import csv
import sys

import click

from asl_cli.commands._shared import emit, format_option_with, get_client


@click.group('analytics')
def analytics():
    """Read stored learning analytics (lesson opens, not video viewing)."""


def _fetch_pages(client, path, params, fetch_all):
    data = client.get(path, params=params)
    rows = list(data['activities'])
    seen = set()
    while fetch_all and data.get('next_cursor'):
        cursor = data['next_cursor']
        if cursor in seen:
            raise click.ClickException('API repeated a cursor; export is incomplete')
        seen.add(cursor)
        params = {**params, 'cursor': cursor}
        data = client.get(path, params=params)
        rows.extend(data['activities'])
    data['activities'] = rows
    data['count'] = len(rows)
    return data


def _flat_row(event):
    unit = event.get('unit') or {}
    user = event['user']
    return {
        'id': event['id'], 'occurred_at': event['occurred_at'],
        'email': user['email'], 'name': user['name'],
        'module': unit.get('module_title'), 'unit': unit.get('title'),
        'optional_module': unit.get('optional_module'),
        'is_bonus': unit.get('is_bonus'), 'url': event['target_url'],
    }


def _output(data, fmt):
    if fmt in ('csv', 'table'):
        rows = list(map(_flat_row, data['activities']))
        if fmt == 'table':
            emit(rows, fmt)
            return
        writer = csv.DictWriter(sys.stdout, fieldnames=list(_flat_row({
            'id': None, 'occurred_at': None, 'user': {'email': '', 'name': ''},
            'target_url': '',
        })), lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row(row))
        return
    emit(data, fmt)


def _csv_row(row):
    """Names and titles are untrusted text, never spreadsheet formulas."""
    result = {}
    for name, value in row.items():
        if isinstance(value, str):
            if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
                value = "'" + value
        result[name] = value
    return result


@analytics.command('course-activity')
@click.argument('course')
@click.option('--cohort', help='Current cohort enrollment key, e.g. 4.')
@click.option('--module', 'module_id', type=click.IntRange(min=1))
@click.option('--unit', 'unit_id', type=click.IntRange(min=1))
@click.option('--email', help='One member primary email.')
@click.option('--since', help='Inclusive ISO datetime with timezone.')
@click.option('--until', help='Exclusive ISO datetime with timezone.')
@click.option('--limit', type=click.IntRange(1, 500), default=100)
@click.option('--cursor', help='Resume a page with the same filters.')
@click.option('--all', 'fetch_all', is_flag=True, help='Retrieve every cursor page sequentially.')
@format_option_with('csv')
def course_activity(course, cohort, module_id, unit_id, email, since, until,
                    limit, cursor, fetch_all, fmt):
    """Export retained lesson openings; not playback or manual completion."""
    params = {'limit': limit}
    values = {'cohort': cohort, 'module': module_id, 'unit': unit_id,
              'email': email, 'since': since, 'until': until, 'cursor': cursor}
    for name, value in values.items():
        if value is not None:
            params[name] = value
    data = _fetch_pages(get_client(), f'/api/courses/{course}/learning-activity', params, fetch_all)
    _output(data, fmt)


groups = [analytics]
