"""Member-safe Markdown export for sprint plans."""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone

from accounts.utils.display import display_name
from plans.models import NEXT_STEP_KIND_NEXT_STEP, NEXT_STEP_KIND_PRE_SPRINT
from plans.resource_display import normalize_resource_display


def markdown_filename_for_plan(plan):
    """Return the deterministic attachment filename for ``plan``."""
    return f'sprint-plan-{plan.sprint.slug}-{plan.pk}.md'


def render_plan_markdown_export(plan):
    """Render a portable, member-safe Markdown copy of ``plan``.

    This helper intentionally reads only member-visible plan fields and
    participant week notes. It does not query CRM, interview notes, comments,
    onboarding context, Slack ingest state, or Studio-only profile context.
    """
    exported = timezone.localdate().isoformat()
    progress = plan.weeks.aggregate(
        total=Count(
            'checkpoints',
            filter=~Q(checkpoints__description__regex=r'^\s*$'),
        ),
        done=Count(
            'checkpoints',
            filter=(
                Q(checkpoints__done_at__isnull=False)
                & ~Q(checkpoints__description__regex=r'^\s*$')
            ),
        ),
    )
    lines = [
        f'# {_heading_text(plan.display_title)}',
        '',
        f'- Sprint: {_metadata_text(plan.sprint.name)}',
        f'- Member: {_metadata_text(display_name(plan.member))}',
        f'- Exported: {exported}',
        f'- Visibility: {_visibility_label(plan.visibility)}',
        (
            '- Progress: '
            f'{progress["done"] or 0} of {progress["total"] or 0} '
            'checkpoints done'
        ),
        '',
        '## Goal',
        _block_or_placeholder(plan.goal, '_No goal yet._'),
        '',
        '## Summary',
        '### Current situation',
        _block_or_placeholder(plan.summary_current_situation),
        '',
        '### Goal for this sprint',
        _block_or_placeholder(plan.summary_goal),
        '',
        '### Main gap',
        _block_or_placeholder(plan.summary_main_gap),
        '',
        '### Weekly time commitment',
        _block_or_placeholder(plan.summary_weekly_hours),
        '',
        '### Why this plan',
        _block_or_placeholder(plan.summary_why_this_plan),
        '',
        '## Focus',
        _block_or_placeholder(plan.focus_main),
    ]
    lines.extend(_focus_supporting_lines(plan.focus_supporting))
    lines.extend(['', '## Timeline'])
    lines.extend(_timeline_lines(plan))
    lines.extend(['', '## Resources'])
    lines.extend(_resource_lines(plan))
    lines.extend(['', '## Deliverables'])
    lines.extend(_checkbox_lines(
        plan.deliverables.order_by('position', 'id'),
        text_attr='description',
        empty='No deliverables yet.',
    ))
    lines.extend([
        '',
        '## Accountability',
        _block_or_placeholder(plan.accountability),
        '',
        '## Pre-sprint actions',
    ])
    lines.extend(_checkbox_lines(
        plan.next_steps.filter(kind=NEXT_STEP_KIND_PRE_SPRINT).order_by(
            'position', 'id',
        ),
        text_attr='description',
        empty='No pre-sprint actions yet.',
    ))
    lines.extend(['', '## Next steps'])
    lines.extend(_checkbox_lines(
        plan.next_steps.filter(kind=NEXT_STEP_KIND_NEXT_STEP).order_by(
            'position', 'id',
        ),
        text_attr='description',
        empty='No next steps yet.',
    ))
    return '\n'.join(lines).rstrip() + '\n'


def _metadata_text(value):
    return ' '.join(str(value or '').split())


def _heading_text(value):
    return _metadata_text(value) or 'Sprint plan'


def _block_or_placeholder(value, placeholder='_Not specified._'):
    text = str(value or '').strip()
    return text or placeholder


def _visibility_label(value):
    return 'Shared with cohort' if value == 'cohort' else 'Private'


def _focus_supporting_lines(items):
    cleaned = [
        str(item).strip()
        for item in (items or [])
        if str(item or '').strip()
    ]
    if not cleaned:
        return ['- _No supporting focus items yet._']
    return [_bullet_line(item) for item in cleaned]


def _timeline_lines(plan):
    weeks = plan.weeks.order_by('position', 'week_number')
    if not weeks.exists():
        return ['_No weeks yet._']

    lines = []
    for index, week in enumerate(weeks):
        if index:
            lines.append('')
        heading = f'### Week {week.week_number}'
        theme = _metadata_text(week.theme)
        if theme:
            heading = f'{heading}: {theme}'
        lines.append(heading)
        checkpoints = week.checkpoints.meaningful().order_by('position', 'id')
        lines.extend(_checkbox_lines(
            checkpoints,
            text_attr='description',
            empty='No checkpoints yet.',
        ))
        lines.extend([
            '',
            '#### Week notes',
            _week_note_body(week),
        ])
    return lines


def _week_note_body(week):
    note = week.notes.order_by('-created_at').first()
    if note is None:
        return '_No notes yet._'
    return _block_or_placeholder(note.body, '_No notes yet._')


def _resource_lines(plan):
    resources = plan.resources.order_by('position', 'id')
    if not resources.exists():
        return ['- _No resources yet._']
    lines = []
    for resource in resources:
        display = normalize_resource_display(
            resource.title,
            resource.url,
            resource.note,
        )
        title = _metadata_text(display.title) or 'Resource'
        if display.url:
            label = f'[{_escape_link_label(title)}]({display.url})'
        else:
            label = title
        note = str(display.note or '').strip()
        if note:
            label = f'{label} — {note}'
        lines.append(_bullet_line(label))
    return lines


def _checkbox_lines(items, *, text_attr, empty):
    rows = list(items)
    if not rows:
        return [f'- _{empty}_']
    lines = []
    for item in rows:
        checked = 'x' if item.done_at else ' '
        value = str(getattr(item, text_attr, '') or '').strip()
        lines.append(_bullet_line(value or 'Untitled item', prefix=f'- [{checked}] '))
    return lines


def _bullet_line(value, *, prefix='- '):
    return prefix + _indent_continuation_lines(str(value or '').strip())


def _indent_continuation_lines(value):
    if '\n' not in value:
        return value
    first, *rest = value.splitlines()
    return '\n'.join([first, *(f'  {line}' for line in rest)])


def _escape_link_label(value):
    return value.replace('[', r'\[').replace(']', r'\]')
