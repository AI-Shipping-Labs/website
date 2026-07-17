"""Studio views for user impersonation."""

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.return_context import get_next_url, should_skip_logout_redirect
from studio.decorators import staff_required

User = get_user_model()
logger = logging.getLogger(__name__)


def impersonate_as(request, target_user):
    """Switch this browser session to ``target_user`` and remember the staff id."""
    impersonator_id = request.user.pk
    login(request, target_user, backend='django.contrib.auth.backends.ModelBackend')
    # Set after login() because login() cycles the session key.
    request.session['_impersonator_id'] = impersonator_id
    return impersonator_id


@staff_required
@require_POST
def impersonate_user(request, user_id):
    """Log the admin in as the target user, storing the admin's ID in session."""
    target_user = get_object_or_404(User, pk=user_id)
    actor = request.user
    if target_user.is_superuser:
        logger.warning(
            'studio_impersonation_refused_superuser '
            'actor_id=%s actor_email=%s target_id=%s target_email=%s',
            actor.pk,
            actor.email,
            target_user.pk,
            target_user.email,
        )
        messages.error(request, 'Cannot log in as a superuser.')
        return redirect('studio_user_detail', user_id=target_user.pk)

    impersonate_as(request, target_user)
    logger.info(
        'studio_impersonation_started '
        'actor_id=%s actor_email=%s target_id=%s target_email=%s',
        actor.pk,
        actor.email,
        target_user.pk,
        target_user.email,
    )
    return redirect('/')


@require_POST
def stop_impersonation(request):
    """Restore the original admin session."""
    impersonator_id = request.session.get('_impersonator_id')
    if not impersonator_id:
        return redirect('/')

    admin_user = User.objects.filter(
        pk=impersonator_id,
        is_active=True,
        is_staff=True,
    ).first()
    if admin_user is None:
        logout(request)
        return redirect('/')

    # login() cycles the session, which removes _impersonator_id automatically.
    login(request, admin_user, backend='django.contrib.auth.backends.ModelBackend')

    next_url = get_next_url(request, default='/')
    if next_url == '/' or should_skip_logout_redirect(next_url):
        return redirect('/')
    return redirect(next_url)
