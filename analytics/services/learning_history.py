"""Read stored lesson openings without the CRM timeline's 100-row ceiling."""

from django.core import signing
from django.db.models import CharField, Max, Q
from django.db.models.functions import Cast

from analytics.models import UserActivity
from content.models import CohortEnrollment
from content.models.course import Unit

CURSOR_SALT = 'analytics.learning-history.v1'


def history_queryset(course, filters):
    """Cohort is current membership; unit metadata is current syllabus state."""
    units = Unit.objects.filter(module__course=course)
    if filters.get('module'):
        module = filters['module']
        units = units.filter(Q(module_id=module) | Q(module__parent_id=module))
    if filters.get('unit'):
        units = units.filter(pk=filters['unit'])
    unit_keys = units.annotate(activity_key=Cast('pk', CharField())).values('activity_key')
    scope = Q(target_url__startswith=f'/courses/{course.slug}/')
    scope |= Q(target_url='', object_id__in=unit_keys)
    if filters.get('module') or filters.get('unit'):
        scope &= Q(object_id__in=unit_keys)
    rows = UserActivity.objects.filter(
        scope, event_type=UserActivity.EVENT_LESSON_OPEN, object_type='unit',
    )
    if filters.get('cohort'):
        members = CohortEnrollment.objects.filter(cohort=filters['cohort'])
        rows = rows.filter(user_id__in=members.values('user_id'))
    for field, lookup in (('since', 'occurred_at__gte'), ('until', 'occurred_at__lt')):
        if filters.get(field):
            rows = rows.filter(**{lookup: filters[field]})
    if filters.get('email'):
        rows = rows.filter(user__email__iexact=filters['email'])
    return rows


def history_page(queryset, *, limit, cursor, scope):
    """Insertion-order keyset pagination excludes writes after page one."""
    if cursor:
        state = signing.loads(cursor, salt=CURSOR_SALT)
        if state.get('scope') != scope:
            raise signing.BadSignature('Cursor filters do not match')
    else:
        maximum = queryset.aggregate(value=Max('id'))['value'] or 0
        state = {'maximum': maximum, 'before': maximum + 1, 'scope': scope}
    snapshot = queryset.filter(id__lte=state['maximum'])
    page = list(snapshot.filter(id__lt=state['before']).order_by('-id')[:limit + 1])
    next_cursor = None
    if len(page) > limit:
        page = page[:limit]
        state['before'] = page[-1].id
        next_cursor = signing.dumps(state, salt=CURSOR_SALT, compress=True)
    return page, snapshot.count(), next_cursor
