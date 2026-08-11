"""Studio CRUD for user-facing Call profiles (#1404)."""

from django.contrib import messages
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from community.models import CallHost
from studio.decorators import staff_required
from studio.forms import CallProfileForm


def _render_form(request, *, host, form):
    return render(request, 'studio/call_hosts/form.html', {
        'host': host,
        'form': form,
        'form_action': 'edit' if host is not None else 'create',
        'primary_label': 'Save changes' if host is not None else 'Create call profile',
    })


@staff_required
@require_GET
def call_host_list(request):
    """List Call profiles in configured display order."""
    hosts = CallHost.objects.order_by('order', 'name')
    return render(request, 'studio/call_hosts/list.html', {'hosts': hosts})


@staff_required
@require_http_methods(['GET', 'POST'])
def call_host_create(request):
    """Create a Call profile without touching legacy capacity fields."""
    if request.method == 'POST':
        form = CallProfileForm(request.POST)
        if form.is_valid():
            host = form.save()
            messages.success(request, f'Call profile “{host.name}” created.')
            return redirect('studio_call_host_list')
    else:
        form = CallProfileForm(instance=CallHost())
    return _render_form(request, host=None, form=form)


@staff_required
@require_http_methods(['GET', 'POST'])
def call_host_edit(request, host_id):
    """Edit a Call profile without changing legacy capacity/load data."""
    host = get_object_or_404(CallHost, pk=host_id)
    if request.method == 'POST':
        form = CallProfileForm(request.POST, instance=host)
        if form.is_valid():
            host = form.save()
            messages.success(request, f'Call profile “{host.name}” updated.')
            return redirect('studio_call_host_list')
    else:
        form = CallProfileForm(instance=host)
    return _render_form(request, host=host, form=form)


@staff_required
@require_POST
def call_host_delete(request, host_id):
    """Delete an unused profile while preserving all booked-call history."""
    with transaction.atomic():
        host = get_object_or_404(CallHost.objects.select_for_update(), pk=host_id)
        name = host.name
        if host.booked_calls.exists():
            messages.error(
                request,
                "This call profile has booked-call history and can't be deleted. "
                'Turn off “Show on Request a call” to hide it instead.',
            )
            return redirect('studio_call_host_edit', host_id=host.pk)
        try:
            host.delete()
        except ProtectedError:
            messages.error(
                request,
                "This call profile has booked-call history and can't be deleted. "
                'Turn off “Show on Request a call” to hide it instead.',
            )
            return redirect('studio_call_host_edit', host_id=host.pk)

    messages.success(request, f'Call profile “{name}” deleted.')
    return redirect('studio_call_host_list')
