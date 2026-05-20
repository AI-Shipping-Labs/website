"""Shared helper for the ``Open in Studio`` link on Django admin pages.

Reverse direction of issue #702 (which added an ``Open in Django admin``
chip to every Studio detail/edit page). For each Django admin change
form / changelist whose model has a matching Studio detail or edit URL,
expose a one-click jump back into the Studio surface.

The helper is intentionally tiny: a single ``studio_link(obj, url_name,
kwargs_func)`` function that resolves the Studio URL and wraps it in an
anchor that opens in a new tab. ``NoReverseMatch`` is swallowed so a
mistyped URL name renders an empty cell instead of a 500. Per-admin
wiring is two lines:

    class SprintAdmin(admin.ModelAdmin):
        @admin.display(description='Studio')
        def studio_link(self, obj):
            return studio_link(
                obj,
                'studio_sprint_detail',
                lambda o: {'sprint_id': o.pk},
            )

        readonly_fields = (..., 'studio_link')
        list_display = (..., 'studio_link')
"""

from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html


def studio_link(obj, url_name, kwargs_func=None):
    """Return an ``Open in Studio`` anchor for ``obj`` or an empty string.

    Arguments:
        obj: the model instance the admin is rendering. May be ``None``
            on the changelist's "add" row or for unsaved instances.
        url_name: the Studio URL name to reverse (e.g.
            ``studio_sprint_detail``).
        kwargs_func: callable that builds the kwargs dict from ``obj``.
            Defaults to ``{'pk': obj.pk}`` for the common single-int case.

    Returns:
        An ``<a target="_blank" rel="noopener">`` anchor wrapped in
        ``format_html`` (auto-escaped), or an empty string when:

        - ``obj`` is ``None`` or has no ``pk`` (unsaved instance).
        - ``reverse(url_name, ...)`` raises ``NoReverseMatch`` (the
          configured URL was renamed or removed). Self-suppression
          mirrors the #702 pattern so admin pages keep rendering when a
          Studio URL is in flux.
    """
    if obj is None or not getattr(obj, 'pk', None):
        return ''
    if kwargs_func is None:
        def kwargs_func(o):
            return {'pk': o.pk}
    try:
        url = reverse(url_name, kwargs=kwargs_func(obj))
    except NoReverseMatch:
        return ''
    return format_html(
        '<a href="{}" target="_blank" rel="noopener">Open in Studio</a>',
        url,
    )
