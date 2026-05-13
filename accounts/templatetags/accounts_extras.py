"""Template tags for the accounts app (issue #440).

Provides the ``display_name`` filter used on cohort-facing pages, the
``logout_url`` simple tag used by the header to keep users on the
same page after sign-out (issue #519), and the symmetric ``login_url``
simple tag that captures the current path as ``?next=`` so a user who
clicks Sign in from a deep page returns to that page after auth
(issue #594). Also provides product-surface button class constants for
dashboard, account, and sprint plan templates.
"""

from django import template
from django.template import TemplateSyntaxError
from django.urls import reverse

from accounts.return_context import (
    append_next,
    should_skip_logout_redirect,
)
from accounts.utils.display import display_name as _display_name

register = template.Library()

PRODUCT_BUTTON_CLASSES = {
    'primary': (
        'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md '
        'bg-accent px-4 py-2 text-sm font-medium text-accent-foreground '
        'transition-colors hover:bg-accent/90 disabled:cursor-not-allowed '
        'disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 '
        'focus-visible:ring-accent focus-visible:ring-offset-2 '
        'focus-visible:ring-offset-background'
    ),
    'secondary': (
        'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md '
        'border border-border bg-transparent px-4 py-2 text-sm font-medium '
        'text-foreground transition-colors hover:bg-secondary '
        'disabled:cursor-not-allowed disabled:opacity-50 '
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent '
        'focus-visible:ring-offset-2 focus-visible:ring-offset-background'
    ),
    'destructive': (
        'inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md '
        'border border-red-500/30 bg-transparent px-4 py-2 text-sm font-medium '
        'text-red-400 transition-colors hover:bg-red-500/10 '
        'disabled:cursor-not-allowed disabled:opacity-50 '
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent '
        'focus-visible:ring-offset-2 focus-visible:ring-offset-background'
    ),
}


@register.simple_tag
def button_classes(variant, extra_classes=''):
    """Return canonical product-surface button classes.

    Used for dense authenticated surfaces where CTAs should share the same
    44px tap target while retaining per-call-site layout classes.
    """
    try:
        classes = PRODUCT_BUTTON_CLASSES[variant]
    except KeyError as exc:
        raise TemplateSyntaxError(
            f"Unknown button_classes variant: {variant!r}",
        ) from exc
    if extra_classes:
        return f'{classes} {extra_classes}'
    return classes


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


@register.simple_tag(takes_context=True)
def login_url(context):
    """Return the ``Sign in`` link target with a safe ``?next=`` appended.

    Mirrors :func:`logout_url`. The header renders the same Sign-in link
    on every page; this tag computes the appropriate ``next`` value so
    that a visitor who clicks Sign in from a public detail page
    (``/blog/<slug>``, ``/courses/<slug>``, ``/events/<slug>``, etc.)
    lands back on that page after successful authentication. Returns
    the plain login URL with no query string when the current path is
    ``/`` (round-trip is pointless), or when the current path is on the
    auth/member/staff exclusion list — see
    :func:`accounts.return_context.should_skip_logout_redirect`. The
    same exclusion list is reused intentionally: setting ``next`` to a
    path whose anonymous variant is meaningless (the login page itself,
    member-only settings, Studio, Django admin, the notifications feed)
    would either bounce the user back through auth or land them on a
    page they cannot view.

    Open-redirect safety: ``append_next`` runs the candidate through
    :func:`accounts.return_context.sanitize_next_url`, which is stricter
    than Django's ``url_has_allowed_host_and_scheme`` — it requires a
    leading ``/``, rejects protocol-relative ``//`` URLs, rejects
    backslashes and control characters, and rejects any URL with a
    scheme or netloc. We deliberately do NOT loosen this to
    ``url_has_allowed_host_and_scheme`` because absolute URLs to the
    site's own host broaden the attack surface unnecessarily — local
    paths are sufficient for this flow. Issue #594.
    """
    base = reverse("account_login")
    request = context.get("request")
    if request is None:
        return base
    current = request.get_full_path()
    if current == "/" or should_skip_logout_redirect(current):
        return base
    return append_next(base, current)
