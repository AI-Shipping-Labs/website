"""Template tag for resolving the Django-admin change URL of any model
instance from a Studio template (issue #702).

Mirrors #667 in the opposite direction: every Studio edit/detail page
that operates on a specific model instance can opt-in to an "Open in
Django admin" chip just by including the shared ``_admin_link.html``
partial; that partial calls ``{% admin_change_url obj %}`` to decide
whether the chip renders at all.
"""

from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()


@register.simple_tag
def admin_change_url(obj):
    """Return ``/admin/<app>/<model>/<pk>/change/`` for ``obj`` or ``''``.

    Returns an empty string when:

    - ``obj`` is ``None`` or has no ``pk`` (e.g. an unsaved instance).
    - ``obj`` has no ``_meta`` (i.e. it is not a model instance at all).
    - The model is not registered in Django admin, so the
      ``admin:<app>_<model>_change`` URL name does not resolve.

    The ``NoReverseMatch`` fallback means surfaces whose model is not in
    the admin registry render nothing, no per-template skip list needed.
    """
    if obj is None or not getattr(obj, 'pk', None):
        return ''
    meta = getattr(obj, '_meta', None)
    if meta is None:
        return ''
    try:
        return reverse(
            f'admin:{meta.app_label}_{meta.model_name}_change',
            args=[obj.pk],
        )
    except NoReverseMatch:
        return ''
