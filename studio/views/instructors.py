"""Studio views for instructor account linking (issue #1345).

Surfaces the instructors whose ``user`` FK is unlinked so an operator can link
them to the platform account that should receive #1341 ``content_comment``
notifications. Manual linking reuses the existing ``studio_user_search``
people picker. Django admin remains the low-level fallback.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from content.models import Instructor
from content.services.instructor_linking import comment_content_count
from studio.decorators import staff_required

User = get_user_model()

FILTERS = {'all', 'unlinked', 'needs_link'}

EMPTY_STATES = {
    'all': 'No instructors have been synced yet.',
    'unlinked': (
        'Every instructor is linked to a platform account. Nothing to do here.'
    ),
    'needs_link': (
        'No unlinked instructors have comment-bearing content. The highest-'
        'value linking work is done.'
    ),
}


@staff_required
def instructor_list(request):
    """List instructors with link state and comment-bearing content counts."""
    active_filter = request.GET.get('filter', 'all')
    if active_filter not in FILTERS:
        active_filter = 'all'

    qs = Instructor.objects.select_related('user').order_by('name')
    if active_filter in {'unlinked', 'needs_link'}:
        qs = qs.filter(user__isnull=True)

    rows = []
    for instructor in qs:
        count = comment_content_count(instructor)
        if active_filter == 'needs_link' and count == 0:
            continue
        rows.append({'instructor': instructor, 'comment_count': count})

    return render(request, 'studio/instructors/list.html', {
        'rows': rows,
        'active_filter': active_filter,
        'empty_state': EMPTY_STATES[active_filter],
    })


def _list_url(active_filter):
    base = reverse('studio_instructor_list')
    return f'{base}?filter={active_filter}'


def _redirect_to_list(request):
    active_filter = request.POST.get('filter', 'all')
    if active_filter not in FILTERS:
        active_filter = 'all'
    return redirect(_list_url(active_filter))


@staff_required
def instructor_link(request, instructor_id):
    """Set ``Instructor.user`` from the submitted user id (manual override)."""
    instructor = get_object_or_404(Instructor, instructor_id=instructor_id)
    if request.method != 'POST':
        return redirect(_list_url('all'))

    raw_user_id = (request.POST.get('user_id') or '').strip()
    if not raw_user_id:
        messages.error(request, 'Select an account to link.')
        return _redirect_to_list(request)

    user = User.objects.filter(pk=raw_user_id).first()
    if user is None:
        messages.error(request, 'That account was not found.')
        return _redirect_to_list(request)

    instructor.user = user
    instructor.save(update_fields=['user'])
    messages.success(
        request, f'Linked {instructor.name} to {user.email}.'
    )
    return _redirect_to_list(request)


@staff_required
def instructor_unlink(request, instructor_id):
    """Clear ``Instructor.user``."""
    instructor = get_object_or_404(Instructor, instructor_id=instructor_id)
    if request.method != 'POST':
        return redirect(_list_url('all'))

    instructor.user = None
    instructor.save(update_fields=['user'])
    messages.success(request, f'Unlinked {instructor.name}.')
    return _redirect_to_list(request)
