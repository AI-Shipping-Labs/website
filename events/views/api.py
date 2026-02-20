from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_http_methods

from content.access import can_access
from events.models import Event, EventRegistration


@require_POST
def register_for_event(request, slug):
    """Register the authenticated user for an event.

    Returns:
        201 on success
        401 if not authenticated
        403 if tier too low
        404 if event not found or draft
        409 if already registered
        410 if event is full
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    event = get_object_or_404(Event, slug=slug)
    if event.status == 'draft':
        return JsonResponse({'error': 'Event not found'}, status=404)

    # Check access (tier level)
    if not can_access(request.user, event):
        return JsonResponse(
            {'error': 'Insufficient access level'},
            status=403,
        )

    # Check if already registered
    if EventRegistration.objects.filter(
        event=event, user=request.user,
    ).exists():
        return JsonResponse(
            {'error': 'Already registered'},
            status=409,
        )

    # Check capacity
    if event.is_full:
        return JsonResponse(
            {'error': 'Event is full'},
            status=410,
        )

    # Register the user
    registration = EventRegistration.objects.create(
        event=event, user=request.user,
    )

    return JsonResponse({
        'status': 'registered',
        'event_slug': event.slug,
        'registered_at': registration.registered_at.isoformat(),
    }, status=201)


@require_http_methods(['DELETE'])
def unregister_from_event(request, slug):
    """Unregister the authenticated user from an event.

    Returns:
        200 on success
        401 if not authenticated
        404 if event not found or not registered
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    event = get_object_or_404(Event, slug=slug)

    deleted_count, _ = EventRegistration.objects.filter(
        event=event, user=request.user,
    ).delete()

    if deleted_count == 0:
        return JsonResponse(
            {'error': 'Not registered for this event'},
            status=404,
        )

    return JsonResponse({
        'status': 'unregistered',
        'event_slug': event.slug,
    })
