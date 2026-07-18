"""Read-only sprint roster activity selector.

The selector keeps Studio and staff-token API roster activity in sync:
merged enrollment/plan rows, checkpoint progress, latest member activity,
and current sprint-week triage state are computed once here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from accounts.utils.display import display_name
from crm.models import SlackMessage
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    SprintEnrollment,
    WeekNote,
)

ACTIVITY_FILTER_NO_UPDATE_THIS_WEEK = 'no_update_this_week'

_SOURCE_LABELS = {
    'slack': 'Slack update',
    'week_note': 'Week note',
    'checkpoint': 'Checkpoint completed',
    'deliverable': 'Deliverable completed',
    'next_step': 'Next step completed',
}
_SOURCE_PRIORITY = {
    'slack': 5,
    'week_note': 4,
    'checkpoint': 3,
    'deliverable': 2,
    'next_step': 1,
}


@dataclass(frozen=True)
class CurrentSprintWeek:
    active: bool
    week_number: int | None
    week_start: datetime | None
    week_end: datetime | None


def _iso(value):
    return value.isoformat() if value else None


def _current_sprint_week(sprint) -> CurrentSprintWeek:
    today = timezone.localdate()
    end_date = sprint.end_date
    if not sprint.start_date or not end_date:
        return CurrentSprintWeek(False, None, None, None)
    if not (sprint.start_date <= today < end_date):
        return CurrentSprintWeek(False, None, None, None)

    week_number = ((today - sprint.start_date).days // 7) + 1
    week_start_date = sprint.start_date + timedelta(weeks=week_number - 1)
    week_end_date = min(week_start_date + timedelta(days=7), end_date)
    tz = timezone.get_current_timezone()
    week_start = timezone.make_aware(
        datetime.combine(week_start_date, time.min),
        tz,
    )
    week_end_boundary = timezone.make_aware(
        datetime.combine(week_end_date, time.min),
        tz,
    )
    week_end = min(timezone.now(), week_end_boundary)
    return CurrentSprintWeek(True, week_number, week_start, week_end)


def _build_merged_rows(*, sprint, enrollments, plans):
    rows_by_user_id = {}

    for index, enrollment in enumerate(enrollments):
        rows_by_user_id[enrollment.user_id] = {
            'member': enrollment.user,
            'enrollment': enrollment,
            'plan': None,
            'create_plan_url': (
                reverse('studio_plan_create')
                + '?'
                + urlencode({
                    'user': enrollment.user_id,
                    'sprint': sprint.pk,
                })
            ),
            'sort_group': 0,
            'sort_index': index,
        }

    for index, plan in enumerate(plans):
        row = rows_by_user_id.get(plan.member_id)
        if row is None:
            rows_by_user_id[plan.member_id] = {
                'member': plan.member,
                'enrollment': None,
                'plan': plan,
                'create_plan_url': '',
                'sort_group': 1,
                'sort_index': index,
            }
        else:
            row['plan'] = plan

    rows = sorted(
        rows_by_user_id.values(),
        key=lambda row: (
            row['sort_group'],
            row['sort_index'],
            row['member'].pk,
        ),
    )
    for index, row in enumerate(rows):
        row['original_index'] = index
    return rows


def _checkpoint_progress(plan_ids):
    if not plan_ids:
        return {}
    rows = (
        Checkpoint.objects
        .meaningful()
        .filter(week__plan_id__in=plan_ids)
        .values('week__plan_id')
        .annotate(
            total=Count('id'),
            done=Count('id', filter=Q(done_at__isnull=False)),
            latest=Max('done_at'),
        )
    )
    return {
        row['week__plan_id']: {
            'total': row['total'],
            'done': row['done'],
            'latest': row['latest'],
        }
        for row in rows
    }


def _max_by_plan(queryset, plan_field, timestamp_field='done_at'):
    rows = (
        queryset
        .values(plan_field)
        .annotate(latest=Max(timestamp_field))
    )
    return {
        row[plan_field]: row['latest']
        for row in rows
        if row['latest'] is not None
    }


def _latest_activity_by_plan(plan_ids):
    if not plan_ids:
        return {}

    source_maps = {
        'slack': _max_by_plan(
            SlackMessage.objects.filter(thread__plan_id__in=plan_ids),
            'thread__plan_id',
            'posted_at',
        ),
        'week_note': _max_by_plan(
            WeekNote.objects.filter(week__plan_id__in=plan_ids),
            'week__plan_id',
            'updated_at',
        ),
        'checkpoint': {
            plan_id: data['latest']
            for plan_id, data in _checkpoint_progress(plan_ids).items()
            if data['latest'] is not None
        },
        'deliverable': _max_by_plan(
            Deliverable.objects.filter(
                plan_id__in=plan_ids,
                done_at__isnull=False,
            ),
            'plan_id',
        ),
        'next_step': _max_by_plan(
            NextStep.objects.filter(
                plan_id__in=plan_ids,
                done_at__isnull=False,
            ),
            'plan_id',
        ),
    }

    latest = {}
    for source, values in source_maps.items():
        for plan_id, timestamp in values.items():
            current = latest.get(plan_id)
            candidate = {
                'source': source,
                'source_label': _SOURCE_LABELS[source],
                'timestamp': timestamp,
            }
            if current is None:
                latest[plan_id] = candidate
                continue
            if (
                timestamp,
                _SOURCE_PRIORITY[source],
            ) > (
                current['timestamp'],
                _SOURCE_PRIORITY[current['source']],
            ):
                latest[plan_id] = candidate
    return latest


def build_sprint_roster_activity(sprint, *, activity_filter=''):
    enrollments = list(
        SprintEnrollment.objects
        .filter(sprint=sprint)
        .select_related('user', 'enrolled_by')
        .order_by('enrolled_at', 'pk')
    )
    plans = list(
        Plan.objects
        .filter(sprint=sprint)
        .select_related('member')
        .order_by('created_at', 'pk')
    )
    rows = _build_merged_rows(
        sprint=sprint,
        enrollments=enrollments,
        plans=plans,
    )
    member_count = len(rows)
    plan_ids = [plan.pk for plan in plans]
    progress_by_plan = _checkpoint_progress(plan_ids)
    latest_by_plan = _latest_activity_by_plan(plan_ids)
    current_week = _current_sprint_week(sprint)

    no_update_count = 0
    for row in rows:
        plan = row['plan']
        if plan is None:
            row['progress'] = {
                'done': None,
                'total': None,
                'label': 'No plan',
            }
            row['last_update'] = {
                'source': None,
                'source_label': None,
                'timestamp': None,
                'timestamp_iso': None,
                'label': 'No updates yet',
            }
            row['this_week'] = {
                'status': 'no_plan',
                'label': 'No plan',
                'updated': False,
            }
            row['needs_weekly_update'] = current_week.active
            if row['needs_weekly_update']:
                no_update_count += 1
            continue

        progress = progress_by_plan.get(plan.pk, {})
        done_count = progress.get('done', 0)
        total_count = progress.get('total', 0)
        row['progress'] = {
            'done': done_count,
            'total': total_count,
            'label': f'{done_count}/{total_count} checkpoints',
        }

        latest = latest_by_plan.get(plan.pk)
        if latest is None:
            row['last_update'] = {
                'source': None,
                'source_label': None,
                'timestamp': None,
                'timestamp_iso': None,
                'label': 'No updates yet',
            }
        else:
            row['last_update'] = {
                'source': latest['source'],
                'source_label': latest['source_label'],
                'timestamp': latest['timestamp'],
                'timestamp_iso': _iso(latest['timestamp']),
                'label': latest['source_label'],
            }

        timestamp = row['last_update']['timestamp']
        updated_this_week = (
            current_week.active
            and timestamp is not None
            and current_week.week_start <= timestamp < current_week.week_end
        )
        if not current_week.active:
            this_week = {
                'status': 'no_active_week',
                'label': 'No active sprint week',
                'updated': False,
            }
        elif updated_this_week:
            this_week = {
                'status': 'updated',
                'label': 'Updated this week',
                'updated': True,
            }
        else:
            this_week = {
                'status': 'no_update',
                'label': 'No update this week',
                'updated': False,
            }
        row['this_week'] = this_week
        row['needs_weekly_update'] = (
            current_week.active and not updated_this_week
        )
        if row['needs_weekly_update']:
            no_update_count += 1

    if activity_filter == ACTIVITY_FILTER_NO_UPDATE_THIS_WEEK:
        rows = [row for row in rows if row['needs_weekly_update']]
        rows.sort(
            key=lambda row: (
                0 if (
                    row['plan'] is None
                    or row['last_update']['timestamp'] is None
                ) else 1,
                row['last_update']['timestamp'] or datetime.min.replace(
                    tzinfo=timezone.get_current_timezone(),
                ),
                row['original_index'],
            )
        )

    return {
        'sprint': sprint,
        'current_week': {
            'active': current_week.active,
            'week_number': current_week.week_number,
            'week_start': current_week.week_start,
            'week_end': current_week.week_end,
            'week_start_iso': _iso(current_week.week_start),
            'week_end_iso': _iso(current_week.week_end),
        },
        'totals': {
            'members': member_count,
            'enrolled': len(enrollments),
            'plans': len(plans),
            'no_update_this_week': no_update_count,
        },
        'enrollments': enrollments,
        'plans': plans,
        'rows': rows,
        'activity_filter': activity_filter,
    }


def serialize_roster_activity(activity):
    sprint = activity['sprint']
    return {
        'sprint': {
            'id': sprint.pk,
            'slug': sprint.slug,
            'name': sprint.name,
            'start_date': (
                sprint.start_date.isoformat() if sprint.start_date else None
            ),
            'end_date': sprint.end_date.isoformat() if sprint.end_date else None,
            'duration_weeks': sprint.duration_weeks,
            'status': sprint.status,
        },
        'current_week': {
            'active': activity['current_week']['active'],
            'week_number': activity['current_week']['week_number'],
            'week_start': activity['current_week']['week_start_iso'],
            'week_end': activity['current_week']['week_end_iso'],
        },
        'totals': activity['totals'],
        'members': [_serialize_row(row) for row in activity['rows']],
    }


def _serialize_row(row):
    member = row['member']
    plan = row['plan']
    enrollment = row['enrollment']
    return {
        'member': {
            'id': member.pk,
            'email': member.email,
            'display_name': display_name(member),
        },
        'enrollment': {
            'enrolled': enrollment is not None,
            'id': enrollment.pk if enrollment else None,
            'enrolled_at': _iso(enrollment.enrolled_at) if enrollment else None,
        },
        'plan': {
            'exists': plan is not None,
            'id': plan.pk if plan else None,
            'title': plan.display_title if plan else None,
            'visibility': plan.visibility if plan else None,
            'shared_at': _iso(plan.shared_at) if plan else None,
        },
        'progress': {
            'done': row['progress']['done'],
            'total': row['progress']['total'],
            'label': row['progress']['label'],
        },
        'last_update': {
            'source': row['last_update']['source'],
            'source_label': row['last_update']['source_label'],
            'timestamp': row['last_update']['timestamp_iso'],
            'label': row['last_update']['label'],
        },
        'this_week': row['this_week'],
    }
