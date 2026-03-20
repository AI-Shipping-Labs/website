"""Studio views for managing URL redirects."""

from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from integrations.middleware import clear_redirect_cache
from integrations.models import Redirect
from studio.decorators import staff_required


@staff_required
def redirect_list(request):
    """List all redirects."""
    redirects = Redirect.objects.all()
    return render(request, 'studio/redirects/list.html', {
        'redirects': redirects,
    })


@staff_required
def redirect_create(request):
    """Create a new redirect."""
    if request.method == 'POST':
        source_path = request.POST.get('source_path', '').strip()
        target_path = request.POST.get('target_path', '').strip()
        redirect_type = int(request.POST.get('redirect_type', 301))
        is_active = request.POST.get('is_active') == 'on'

        # Ensure paths start with /
        if source_path and not source_path.startswith('/'):
            source_path = '/' + source_path
        if target_path and not target_path.startswith('/'):
            target_path = '/' + target_path

        if not source_path or not target_path:
            messages.error(request, 'Source path and target path are required.')
            return render(request, 'studio/redirects/form.html', {
                'redirect_obj': None,
                'form_action': 'create',
            })

        if Redirect.objects.filter(source_path=source_path).exists():
            messages.error(request, f'A redirect for "{source_path}" already exists.')
            return render(request, 'studio/redirects/form.html', {
                'redirect_obj': None,
                'form_action': 'create',
            })

        obj = Redirect.objects.create(
            source_path=source_path,
            target_path=target_path,
            redirect_type=redirect_type,
            is_active=is_active,
        )
        clear_redirect_cache()
        messages.success(request, f'Redirect created: {obj.source_path} -> {obj.target_path}')
        return redirect('studio_redirect_list')

    return render(request, 'studio/redirects/form.html', {
        'redirect_obj': None,
        'form_action': 'create',
    })


@staff_required
def redirect_edit(request, redirect_id):
    """Edit an existing redirect."""
    obj = get_object_or_404(Redirect, pk=redirect_id)

    if request.method == 'POST':
        source_path = request.POST.get('source_path', '').strip()
        target_path = request.POST.get('target_path', '').strip()
        redirect_type = int(request.POST.get('redirect_type', 301))
        is_active = request.POST.get('is_active') == 'on'

        if source_path and not source_path.startswith('/'):
            source_path = '/' + source_path
        if target_path and not target_path.startswith('/'):
            target_path = '/' + target_path

        # Check uniqueness excluding self
        if Redirect.objects.filter(source_path=source_path).exclude(pk=obj.pk).exists():
            messages.error(request, f'A redirect for "{source_path}" already exists.')
            return render(request, 'studio/redirects/form.html', {
                'redirect_obj': obj,
                'form_action': 'edit',
            })

        obj.source_path = source_path
        obj.target_path = target_path
        obj.redirect_type = redirect_type
        obj.is_active = is_active
        obj.save()
        clear_redirect_cache()
        messages.success(request, f'Redirect updated: {obj.source_path} -> {obj.target_path}')
        return redirect('studio_redirect_edit', redirect_id=obj.pk)

    return render(request, 'studio/redirects/form.html', {
        'redirect_obj': obj,
        'form_action': 'edit',
    })


@staff_required
@require_POST
def redirect_delete(request, redirect_id):
    """Delete a redirect."""
    obj = get_object_or_404(Redirect, pk=redirect_id)
    source = obj.source_path
    obj.delete()
    clear_redirect_cache()
    messages.success(request, f'Redirect deleted: {source}')
    return redirect('studio_redirect_list')


@staff_required
@require_POST
def redirect_toggle(request, redirect_id):
    """Toggle a redirect's active status."""
    obj = get_object_or_404(Redirect, pk=redirect_id)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    clear_redirect_cache()
    status = 'activated' if obj.is_active else 'deactivated'
    messages.success(request, f'Redirect {status}: {obj.source_path}')
    return redirect('studio_redirect_list')
