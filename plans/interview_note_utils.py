"""Helpers for normalizing staff/member interview notes."""

import re

from accounts.utils.tags import normalize_tags

SLACK_SOURCE_TYPE = 'slack'
SLACK_NOTE_BASE_TAGS = ['slack', 'plan-sprints']


def normalize_note_tags(tags):
    """Normalize note tags with the operator contact-tag rules."""
    return normalize_tags(tags if isinstance(tags, list) else [])


def normalize_source_type(source_type):
    """Keep source types short and comparable."""
    if not isinstance(source_type, str):
        return ''
    return source_type.strip().lower()[:40]


def normalize_source_metadata(source_metadata):
    """Persist only object-shaped source metadata."""
    return source_metadata if isinstance(source_metadata, dict) else {}


def normalize_slack_note_body(body):
    """Strip Slack display noise while preserving meaningful plain text."""
    if not body:
        return ''

    text = str(body).replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\bB_?locker:_\b', 'Blocker:', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'\1', text)

    cleaned_lines = []
    for raw_line in text.split('\n'):
        line = raw_line.strip()
        if re.fullmatch(r'(?:[•*-]\s*)?[.]', line):
            continue
        if re.fullmatch(r'[•*-]\s*', line):
            continue
        cleaned_lines.append(line)

    cleaned = '\n'.join(cleaned_lines)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def normalize_note_body(body, source_type):
    """Apply source-specific body normalization."""
    body = '' if body is None else str(body)
    if normalize_source_type(source_type) == SLACK_SOURCE_TYPE:
        return normalize_slack_note_body(body)
    return body

