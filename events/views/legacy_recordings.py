"""Compatibility redirects for the retired ``/event-recordings`` URLs."""

from urllib.parse import urlencode

from django.http import Http404, HttpResponsePermanentRedirect

from events.services.time_windows import past_recording_events_queryset


def _append_original_query_string(request, target):
    """Append the unchanged request query string to ``target`` when present."""
    query_string = request.META.get('QUERY_STRING', '')
    if not query_string:
        return target
    return f'{target}?{query_string}'


def legacy_recordings_list_redirect(request):
    """Redirect the retired recording catalog to the canonical past filter."""
    query_pairs = [('filter', 'past')]
    for key, values in request.GET.lists():
        if key == 'filter':
            continue
        query_pairs.extend((key, value) for value in values)

    target = f'/events?{urlencode(query_pairs)}'
    return HttpResponsePermanentRedirect(target)


def legacy_recording_detail_redirect(request, slug):
    """Redirect an eligible retired recording detail URL to its replacement."""
    event = (
        past_recording_events_queryset()
        .select_related('workshop')
        .filter(slug=slug)
        .first()
    )
    if event is None:
        raise Http404('Legacy event recording has no public replacement')

    workshop = getattr(event, 'workshop', None)
    if workshop is not None:
        if workshop.status != 'published':
            raise Http404('Linked workshop is not published')
        target = f'{workshop.get_absolute_url()}/video'
    else:
        target = event.get_absolute_url()

    return HttpResponsePermanentRedirect(
        _append_original_query_string(request, target),
    )


def legacy_recording_not_found(request, suffix):
    """Reserve unsupported retired-recording paths as genuine 404s."""
    raise Http404('Unsupported legacy event-recording path')
