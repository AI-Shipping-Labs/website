"""Template tags for the accounts app (issue #440).

Currently provides the ``display_name`` filter used on cohort-facing
pages so a user with no first/last name still renders as their email
local-part (``ada@example.com`` -> ``ada``).
"""

from django import template

from accounts.utils.display import display_name as _display_name

register = template.Library()


@register.filter(name='display_name')
def display_name(user):
    """Return the best human label for ``user``.

    See :func:`accounts.utils.display.display_name` for the order of
    fallbacks. Used in templates as ``{{ user|display_name }}``.
    """
    return _display_name(user)
