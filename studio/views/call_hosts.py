"""Studio views for managing call hosts (#870).

Staff can edit each host's booking link, capacity, current load, active
flag, role label, photo, and display order without a deploy. The
member-facing ``/request-a-call`` page reads these rows live.
"""

from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from community.models import CallHost
from studio.decorators import staff_required


@staff_required
def call_host_list(request):
    """List all call hosts with their derived availability."""
    hosts = CallHost.objects.all()
    return render(request, 'studio/call_hosts/list.html', {'hosts': hosts})


@staff_required
def call_host_edit(request, host_id):
    """Edit a call host's booking link, capacity, and availability."""
    host = get_object_or_404(CallHost, pk=host_id)

    if request.method == 'POST':
        host.name = request.POST.get('name', '').strip() or host.name
        host.slug = request.POST.get('slug', '').strip() or slugify(host.name)
        host.role_label = request.POST.get('role_label', '').strip()
        host.photo_url = request.POST.get('photo_url', '').strip()
        host.booking_url = request.POST.get('booking_url', '').strip()
        host.is_active = request.POST.get('is_active') == 'on'
        host.capacity = int(request.POST.get('capacity', 0) or 0)
        host.current_load = int(request.POST.get('current_load', 0) or 0)
        host.order = int(request.POST.get('order', 0) or 0)
        host.save()
        return redirect('studio_call_host_list')

    return render(request, 'studio/call_hosts/form.html', {'host': host})
