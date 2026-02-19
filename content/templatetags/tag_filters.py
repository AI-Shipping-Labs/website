"""Template tags for tag filtering on listing pages."""

from django import template

register = template.Library()


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
            for key, val in extra_params.items():
                if val:
                    params.append(f'{key}={val}')
    for tag in tags:
        params.append(f'tag={tag}')
    if params:
        return f'{base_path}?{"&".join(params)}'
    return base_path
