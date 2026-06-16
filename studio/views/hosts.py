"""Studio views for managing event hosts (#994)."""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from events.models import Host
from studio.decorators import staff_required


def _host_form_values(host=None):
    if host is None:
        return {
            'name': '',
            'slug': '',
            'bio': '',
            'photo_url': '',
            'email': '',
            'is_active': True,
        }
    return {
        'name': host.name,
        'slug': host.slug,
        'bio': host.bio,
        'photo_url': host.photo_url,
        'email': host.email,
        'is_active': host.is_active,
    }


def _collect_host_values(request, *, host=None):
    values = {
        'name': request.POST.get('name', '').strip(),
        'slug': request.POST.get('slug', '').strip(),
        'bio': request.POST.get('bio', ''),
        'photo_url': request.POST.get('photo_url', '').strip(),
        'email': request.POST.get('email', '').strip(),
        'is_active': request.POST.get('is_active') == 'on',
    }
    values['slug'] = values['slug'] or slugify(values['name'])

    errors = {}
    if not values['name']:
        errors['name'] = 'Name is required.'
    if not values['slug']:
        errors['slug'] = 'Slug is required.'
    duplicate_qs = Host.objects.filter(slug=values['slug'])
    if host is not None:
        duplicate_qs = duplicate_qs.exclude(pk=host.pk)
    if values['slug'] and duplicate_qs.exists():
        errors['slug'] = 'A host with this slug already exists.'
    if values['email']:
        try:
            validate_email(values['email'])
        except ValidationError:
            errors['email'] = 'Must be a valid email address.'
    return values, errors


@staff_required
def host_list(request):
    """List event hosts."""
    hosts = Host.objects.all()
    return render(request, 'studio/hosts/list.html', {'hosts': hosts})


@staff_required
def host_create(request):
    """Create a new event host."""
    errors = {}
    form_values = _host_form_values()

    if request.method == 'POST':
        form_values, errors = _collect_host_values(request)
        if not errors:
            Host.objects.create(**form_values)
            return redirect('studio_host_list')

    return render(request, 'studio/hosts/form.html', {
        'host': None,
        'form_values': form_values,
        'errors': errors,
    })


@staff_required
def host_edit(request, host_id):
    """Edit an event host."""
    host = get_object_or_404(Host, pk=host_id)
    errors = {}
    form_values = _host_form_values(host)

    if request.method == 'POST':
        form_values, errors = _collect_host_values(request, host=host)
        if not errors:
            for field, value in form_values.items():
                setattr(host, field, value)
            host.save()
            return redirect('studio_host_list')

    return render(request, 'studio/hosts/form.html', {
        'host': host,
        'form_values': form_values,
        'errors': errors,
    })
