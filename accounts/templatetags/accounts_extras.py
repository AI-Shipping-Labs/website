"""Template tags for the accounts app (issue #440).

Provides the ``display_name`` filter used on cohort-facing pages and
the ``logout_url`` simple tag used by the header to keep users on the
same page after sign-out (issue #519).
"""

from django import template
from django.urls import reverse

from accounts.return_context import (
    append_next,
    should_skip_logout_redirect,
)
from accounts.utils.display import display_name as _display_name

register = template.Library()


@register.filter(name='display_name')
def display_name(user):
    """Return the best human label for ``user``.

    See :func:`accounts.utils.display.display_name` for the order of
    fallbacks. Used in templates as ``{{ user|display_name }}``.
    """
    return _display_name(user)


@register.simple_tag(takes_context=True)
def logout_url(context):
    """Return the ``Log out`` link target with a safe ``?next=`` appended.

    The header renders the same URL on every page; this tag computes
    the appropriate ``next`` value based on the current request path so
    sign-out from a public detail page (event/course/workshop/blog)
    keeps the user on that page in their anonymous state. Returns the
    plain logout URL with no query string when the current path is on
    the exclusion list (member-only / admin-only surfaces) — see
    :func:`accounts.return_context.should_skip_logout_redirect`.
    Issue #519.
    """
    base = reverse("account_logout")
    request = context.get("request")
    if request is None:
        return base
    current = request.get_full_path()
    # Bare ``/`` is the default logout target — skip the round-trip.
    if current == "/" or should_skip_logout_redirect(current):
        return base
    return append_next(base, current)
