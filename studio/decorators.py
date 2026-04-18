"""Staff-only access decorator for studio views."""

from functools import wraps

from django.shortcuts import redirect


def staff_required(view_func):
    """Decorator that requires the user to be an authenticated staff member.

    Non-authenticated users are redirected to the login page.
    Authenticated non-staff users receive a 403 response.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'/accounts/login/?next={request.path}')
        if not request.user.is_staff:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Staff access required')
        return view_func(request, *args, **kwargs)
    return wrapper


def superuser_required(view_func):
    """Decorator that requires the user to be an authenticated superuser.

    Used for views that can mint other admins (issue #234) -- staff alone is
    not enough because a staff-only user should not be able to grant superuser
    rights to a new account.

    Non-authenticated users are redirected to the login page.
    Authenticated non-superusers receive a 403 response.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'/accounts/login/?next={request.path}')
        if not request.user.is_superuser:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Superuser access required')
        return view_func(request, *args, **kwargs)
    return wrapper
