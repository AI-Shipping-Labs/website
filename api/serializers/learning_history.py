"""Bounded metadata joins for persisted lesson-open events."""

from accounts.utils.display import display_name
from api.serializers.datetime import isoformat_or_none
from content.models.course import Unit


def _unit_details(unit):
    if unit is None:
        return None
    module = unit.module
    optional_module = module.is_bonus
    if module.parent is not None:
        optional_module = optional_module or module.parent.is_bonus
    return {
        'id': unit.id, 'title': unit.title, 'kind': unit.kind,
        'module_id': module.id, 'module_title': module.title,
        'optional_module': optional_module,
        'is_bonus': unit.effective_is_bonus,
    }


def serialize_history(rows):
    unit_ids = []
    for row in rows:
        if row.object_id.isdecimal():
            unit_ids.append(int(row.object_id))
    units = Unit.objects.filter(pk__in=unit_ids).select_related('module__parent')
    by_id = {str(unit.id): unit for unit in units}
    result = []
    for row in rows:
        result.append({
            'id': row.id, 'event_type': row.event_type,
            'occurred_at': isoformat_or_none(row.occurred_at),
            'user': {'email': row.user.email, 'name': display_name(row.user)},
            'label': row.label, 'target_url': row.target_url,
            'unit': _unit_details(by_id.get(row.object_id)),
        })
    return result
