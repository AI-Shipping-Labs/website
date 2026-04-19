"""Studio views for user impersonation."""

from django.contrib.auth import get_user_model, login
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from studio.decorators import staff_required

User = get_user_model()


@staff_required
@require_POST
def impersonate_user(request, user_id):
    """Log the admin in as the target user, storing the admin's ID in session."""
    target_user = get_object_or_404(User, pk=user_id)
    impersonator_id = request.user.pk
    login(request, target_user, backend='django.contrib.auth.backends.ModelBackend')
    # Set after login() because login() cycles the session key
    request.session['_impersonator_id'] = impersonator_id
    return redirect('/')


@require_POST
def stop_impersonation(request):
    """Restore the original admin session."""
    impersonator_id = request.session.get('_impersonator_id')
    if impersonator_id:
        admin_user = get_object_or_404(User, pk=impersonator_id)
        # login() cycles the session, which removes _impersonator_id automatically
        login(request, admin_user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('studio_user_list')
