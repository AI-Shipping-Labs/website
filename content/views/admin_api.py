"""Admin API endpoints for course management.

Provides reordering endpoints for modules and units:
- PUT /api/admin/modules/reorder
- PUT /api/admin/units/reorder

All endpoints require the user to be an authenticated staff member.
"""

import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from content.models import Module, Unit


def _require_staff(request):
    """Return an error JsonResponse if user is not staff, else None."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )
    if not request.user.is_staff:
        return JsonResponse(
            {'error': 'Staff access required'},
            status=403,
        )
    return None


@require_http_methods(["PUT"])
def reorder_modules(request):
    """Reorder modules.

    Expects JSON body: [{"id": 1, "sort_order": 0}, {"id": 2, "sort_order": 1}, ...]
    Updates the sort_order for each module. Returns 200 on success.
    """
    error_response = _require_staff(request)
    if error_response:
        return error_response

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not isinstance(data, list):
        return JsonResponse(
            {'error': 'Expected a list of {id, sort_order} objects'},
            status=400,
        )

    # Validate all entries before applying
    for item in data:
        if not isinstance(item, dict):
            return JsonResponse(
                {'error': 'Each item must be an object with id and sort_order'},
                status=400,
            )
        if 'id' not in item or 'sort_order' not in item:
            return JsonResponse(
                {'error': 'Each item must have id and sort_order fields'},
                status=400,
            )
        try:
            int(item['id'])
            int(item['sort_order'])
        except (ValueError, TypeError):
            return JsonResponse(
                {'error': 'id and sort_order must be integers'},
                status=400,
            )

    # Apply updates
    updated_count = 0
    for item in data:
        module_id = int(item['id'])
        sort_order = int(item['sort_order'])
        rows = Module.objects.filter(pk=module_id).update(sort_order=sort_order)
        updated_count += rows

    return JsonResponse({
        'status': 'ok',
        'updated': updated_count,
    })


@require_http_methods(["PUT"])
def reorder_units(request):
    """Reorder units.

    Expects JSON body: [{"id": 1, "sort_order": 0}, {"id": 2, "sort_order": 1}, ...]
    Updates the sort_order for each unit. Returns 200 on success.
    """
    error_response = _require_staff(request)
    if error_response:
        return error_response

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not isinstance(data, list):
        return JsonResponse(
            {'error': 'Expected a list of {id, sort_order} objects'},
            status=400,
        )

    # Validate all entries before applying
    for item in data:
        if not isinstance(item, dict):
            return JsonResponse(
                {'error': 'Each item must be an object with id and sort_order'},
                status=400,
            )
        if 'id' not in item or 'sort_order' not in item:
            return JsonResponse(
                {'error': 'Each item must have id and sort_order fields'},
                status=400,
            )
        try:
            int(item['id'])
            int(item['sort_order'])
        except (ValueError, TypeError):
            return JsonResponse(
                {'error': 'id and sort_order must be integers'},
                status=400,
            )

    # Apply updates
    updated_count = 0
    for item in data:
        unit_id = int(item['id'])
        sort_order = int(item['sort_order'])
        rows = Unit.objects.filter(pk=unit_id).update(sort_order=sort_order)
        updated_count += rows

    return JsonResponse({
        'status': 'ok',
        'updated': updated_count,
    })
