"""Template tags for tag filtering on listing pages."""

from urllib.parse import quote, urlencode

from django import template

register = template.Library()


@register.filter
def paren_count(value):
    """Render a parenthesized count suffix only when the count is positive.

    Used to keep counter labels clean in the UI (issue #597). The rule:

    - ``{{ 0|paren_count }}``    -> ``""`` (no parens, no trailing space)
    - ``{{ 3|paren_count }}``    -> ``" (3)"`` (note the leading space)
    - ``{{ None|paren_count }}`` -> ``""``
    - Non-numeric input          -> ``""`` (safe default; never raises)

    Typical usage in a template:

        <h3>Enrolled{{ results.enrolled|length|paren_count }}</h3>

    renders as ``Enrolled`` when the list is empty and ``Enrolled (4)``
    when it contains four entries. The leading space lives inside the
    filter so the template stays clean.
    """
    if value is None:
        return ""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f" ({n})"


@register.simple_tag
def tag_add_url(base_path, selected_tags, tag, extra_params=None):
    """Build URL that adds a tag to the current selection.

    Usage: {% tag_add_url base_path selected_tags "python" %}
    """
    tags = list(selected_tags) if selected_tags else []
    if tag not in tags:
        tags.append(tag)
    return _build_url(base_path, tags, extra_params)


@register.simple_tag
def tag_remove_url(base_path, selected_tags, tag, extra_params=None):
    """Build URL that removes a tag from the current selection.

    Usage: {% tag_remove_url base_path selected_tags "python" %}
    """
    tags = [t for t in (selected_tags or []) if t != tag]
    return _build_url(base_path, tags, extra_params)


@register.simple_tag
def tag_clear_url(base_path, extra_params=None):
    """Build URL that clears all tag filters.

    Usage: {% tag_clear_url base_path %}
    """
    return _build_url(base_path, [], extra_params)


def _build_url(base_path, tags, extra_params=None):
    """Build a URL with tag query params."""
    params = []
    if extra_params:
        if isinstance(extra_params, dict):
            extra_items = extra_params.items()
        else:
            extra_items = extra_params
        for key, val in extra_items:
            if val in (None, ''):
                continue
            if isinstance(val, (list, tuple)):
                for item in val:
                    if item not in (None, ''):
                        params.append((key, item))
            else:
                params.append((key, val))
    for tag in tags:
        params.append(('tag', tag))
    if params:
        return f'{base_path}?{urlencode(params, doseq=True, quote_via=quote)}'
    return base_path
