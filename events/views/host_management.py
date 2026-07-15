"""Least-privilege event controls for the currently designated host."""

from django import forms
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from events.models import Event
from events.services.calendar_lifecycle import (
    enqueue_cancellation_update,
    enqueue_schedule_update,
    should_notify_cancellation,
)
from events.services.host_access import (
    HostAccessError,
    HostAccessExpired,
    validate_host_access_token,
)
from events.services.zoom_lifecycle import sync_or_delete_zoom_meeting
from integrations.services.zoom import create_meeting


class HostEventForm(forms.ModelForm):
    """Deliberately limited host-editable event fields."""

    class Meta:
        model = Event
        fields = [
            'title', 'description', 'start_datetime', 'end_datetime',
            'location', 'zoom_join_url', 'status',
        ]
        widgets = {
            'start_datetime': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M',
            ),
            'end_datetime': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M',
            ),
            'description': forms.Textarea(attrs={'rows': 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('start_datetime', 'end_datetime'):
            self.fields[name].input_formats = ['%Y-%m-%dT%H:%M']


def _authorize_host(request, event):
    token = request.GET.get('token') or request.POST.get('token', '')
    try:
        host_user = validate_host_access_token(event, token)
    except HostAccessExpired:
        return None, token, _render_access_denied(request, event, 'expired')
    except HostAccessError:
        return None, token, _render_access_denied(request, event, 'stale')

    if not request.user.is_authenticated:
        return None, token, redirect_to_login(request.get_full_path())
    if request.user.pk != host_user.pk:
        return None, token, _render_access_denied(
            request, event, 'wrong_account',
        )
    return host_user, token, None


def _render_access_denied(request, event, reason):
    """Show a safe recovery route without reflecting the signed token."""
    return render(
        request,
        'events/host_management_denied.html',
        {'event': event, 'reason': reason},
        status=403,
    )


def _manage_url(event, token):
    return f'{reverse("event_host_manage", kwargs={"event_id": event.pk})}?token={token}'


@require_GET
def host_event_manage(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    _host, token, denied = _authorize_host(request, event)
    if denied is not None:
        return denied
    return render(request, 'events/host_management.html', {
        'event': event,
        'token': token,
        'form': HostEventForm(instance=event),
        'registrations': event.registrations.select_related('user').order_by(
            'user__email',
        ),
    })


@require_POST
def host_event_update(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    _host, token, denied = _authorize_host(request, event)
    if denied is not None:
        return denied

    old_event = Event.objects.get(pk=event.pk)
    old_start = old_event.start_datetime
    old_end = old_event.end_datetime
    old_status = old_event.status
    form = HostEventForm(request.POST, instance=event)
    if not form.is_valid():
        return render(request, 'events/host_management.html', {
            'event': event,
            'token': token,
            'form': form,
            'registrations': event.registrations.select_related('user').order_by(
                'user__email',
            ),
        }, status=422)

    event = form.save()
    if should_notify_cancellation(event, old_status):
        enqueue_cancellation_update(event, old_status)
    else:
        enqueue_schedule_update(event, old_start, old_end)
    zoom_error = sync_or_delete_zoom_meeting(event, old_event)
    if zoom_error is not None:
        messages.warning(
            request,
            f'Event saved, but Zoom could not be updated: {zoom_error}',
        )
    messages.success(request, 'Event updated.')
    return redirect(_manage_url(event, token))


@require_POST
def host_event_create_zoom(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    _host, token, denied = _authorize_host(request, event)
    if denied is not None:
        return denied
    if event.platform != 'zoom':
        messages.error(request, 'This event does not use Zoom.')
    elif event.zoom_meeting_id:
        messages.info(request, 'This event already has a Zoom meeting.')
    else:
        try:
            result = create_meeting(event)
            event.zoom_meeting_id = result['meeting_id']
            event.zoom_join_url = result['join_url']
            event.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])
            messages.success(request, 'Zoom meeting created.')
        except Exception:
            messages.error(request, 'Zoom meeting creation failed. Please retry.')
    return redirect(f'{_manage_url(event, token)}#zoom')


@require_POST
def host_event_notify(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    _host, token, denied = _authorize_host(request, event)
    if denied is not None:
        return denied
    from studio.views.notifications import _was_recently_notified
    if _was_recently_notified('event', event):
        messages.info(request, 'Attendees were already notified in the last 24 hours.')
    else:
        from notifications.services import NotificationService
        result = NotificationService.notify(
            'event', event.pk, post_to_slack=False,
        )
        messages.success(
            request, f'Notified {result.get("notified", 0)} members.',
        )
    return redirect(f'{_manage_url(event, token)}#registrations')
