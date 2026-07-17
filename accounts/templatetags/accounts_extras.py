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
    sanitize_next_url,
    should_skip_logout_redirect,
)
from accounts.utils.display import display_name as _display_name

register = template.Library()

# Issue #598 — Canonical product-surface button scale.
#
# The button class string is composed in a fixed order so per-call overrides
# (``extra`` argument) always win the cascade. Three sizes share one base
# shape, three variants share one color treatment, and the rendered string
# interleaves base + size + variant in a single contiguous Tailwind-style
# order: layout, sizing, palette, typography, transitions, state. The
# ``md`` size is the default and matches the previously shipped string
# byte-for-byte so existing call sites do not change.
#
# Joining order in the rendered class string: ``base size variant extra``.
# Do not reshuffle: callers (e.g. the amber verification banner) rely on
# ``extra`` appearing last so ``!``-prefixed utilities override the base.

# Per-size class strings. ``min-h-[44px]`` is intentionally absent from
# ``sm`` — small row actions inside dense tables stay compact.
PRODUCT_BUTTON_SIZE_CLASSES = {
    'sm': 'px-3 py-1.5 text-xs',
    'md': 'min-h-[44px] px-4 py-2 text-sm',
    'lg': 'min-h-[44px] px-6 py-3 text-base',
}

# Per-variant class strings (color treatment only). Layout, padding, and
# text-size come from the size scale; transition / state classes come
# from the base.
PRODUCT_BUTTON_VARIANT_CLASSES = {
    'primary': 'bg-accent text-accent-foreground hover:bg-accent/90',
    'secondary': (
        'border border-border bg-transparent text-foreground hover:bg-secondary'
    ),
    'destructive': (
        'border border-red-500/30 bg-transparent text-red-700 '
        'dark:text-red-400 hover:bg-red-500/10'
    ),
}

# Shared base classes for every product button at every size + variant.
PRODUCT_BUTTON_BASE_CLASSES = (
    'inline-flex items-center justify-center gap-2 rounded-md '
    'font-medium transition-colors disabled:cursor-not-allowed '
    'disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 '
    'focus-visible:ring-accent focus-visible:ring-offset-2 '
    'focus-visible:ring-offset-background'
)


def _compose_button_classes(variant, size):
    return (
        f'{PRODUCT_BUTTON_BASE_CLASSES} '
        f'{PRODUCT_BUTTON_SIZE_CLASSES[size]} '
        f'{PRODUCT_BUTTON_VARIANT_CLASSES[variant]}'
    )


# Backwards-compatible alias: the original constant exposed one class
# string per variant (the ``md`` size). Keep the same shape so callers
# importing ``PRODUCT_BUTTON_CLASSES`` continue to work.
PRODUCT_BUTTON_CLASSES = {
    variant: _compose_button_classes(variant, 'md')
    for variant in PRODUCT_BUTTON_VARIANT_CLASSES
}


@register.simple_tag
def button_classes(variant, size_or_extra='', size='md', extra=''):
    """Return canonical product-surface button classes.

    Signature: ``{% button_classes variant size='md' extra='' %}``.

    Three sizes are supported:

    - ``sm``: ``px-3 py-1.5 text-xs``, no 44px min-height — compact row
      actions and inline edit controls inside dense tables.
    - ``md`` (default): ``min-h-[44px] px-4 py-2 text-sm`` — every
      authenticated CTA on dashboard / account / plan / cohort surfaces.
    - ``lg``: ``min-h-[44px] px-6 py-3 text-base`` — marketing-page hero
      CTAs and pricing conversion buttons.

    Backwards compatibility: the original tag accepted a positional second
    argument that meant ``extra``. To keep every existing call site
    (``{% button_classes 'secondary' 'shrink-0' %}``) byte-for-byte
    unchanged, a positional second argument is treated as ``extra`` when it
    does not match ``{'sm', 'md', 'lg'}``. Callers using the new size
    parameter should always pass it by name: ``size='sm'``.

    The final class string is composed in this order: base, size, variant,
    extra. Per-call overrides therefore win the cascade.
    """
    # Positional second arg compatibility shim: a bare positional string that
    # is not a known size is the legacy ``extra`` argument.
    positional_extra = ''
    if size_or_extra:
        if size_or_extra in PRODUCT_BUTTON_SIZE_CLASSES:
            size = size_or_extra
        else:
            positional_extra = size_or_extra

    if variant not in PRODUCT_BUTTON_VARIANT_CLASSES:
        raise TemplateSyntaxError(
            f"Unknown button_classes variant: {variant!r}. "
            f"Valid variants: {sorted(PRODUCT_BUTTON_VARIANT_CLASSES)}",
        )
    if size not in PRODUCT_BUTTON_SIZE_CLASSES:
        raise TemplateSyntaxError(
            f"Unknown button_classes size: {size!r}. "
            f"Valid sizes: {sorted(PRODUCT_BUTTON_SIZE_CLASSES)}",
        )

    classes = _compose_button_classes(variant, size)
    # The keyword ``extra`` wins over a positional second arg if both are
    # supplied; in practice no call site passes both, but documenting the
    # precedence keeps the behaviour deterministic.
    trailing = extra or positional_extra
    if trailing:
        return f'{classes} {trailing}'
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


@register.simple_tag(takes_context=True)
def impersonation_return_next(context):
    """Return a safe hidden ``next`` value for the impersonation banner."""
    request = context.get("request")
    if request is None:
        return ""
    current = sanitize_next_url(request.get_full_path(), default="")
    if not current or current == "/" or should_skip_logout_redirect(current):
        return ""
    return current
