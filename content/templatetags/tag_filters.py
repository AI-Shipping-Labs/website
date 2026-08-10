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


# Curated display names for tag slugs whose default Title-casing would be
# wrong (acronyms, proper nouns, mixed case). Keys are lower-cased slugs.
_TAG_LABEL_OVERRIDES = {
    'ai': 'AI',
    'aws': 'AWS',
    'llm': 'LLM',
    'llms': 'LLMs',
    'ml': 'ML',
    'ci-cd': 'CI/CD',
    'cicd': 'CI/CD',
    'tts': 'TTS',
    'rag': 'RAG',
    'gpt-4': 'GPT-4',
    'chatgpt': 'ChatGPT',
    'openai': 'OpenAI',
    'dall-e': 'DALL-E',
    'claude-code': 'Claude Code',
    'datatalks-club': 'DataTalks.Club',
    'crisp-dm': 'CRISP-DM',
    'ai-tools': 'AI Tools',
    'ai-agents': 'AI Agents',
    'ai-engineering': 'AI Engineering',
    'ai-engineering-buildcamp': 'AI Engineering Buildcamp',
    'ai-shipping-labs': 'AI Shipping Labs',
    'ml-engineering': 'ML Engineering',
    'llm-zoomcamp': 'LLM Zoomcamp',
    'devops': 'DevOps',
}


@register.filter
def humanize_tag(value):
    """Render a raw tag slug as a human-readable label.

    ``ai-engineering-buildcamp`` -> ``AI Engineering Buildcamp``; acronyms
    and proper nouns come from ``_TAG_LABEL_OVERRIDES`` so we never show a
    raw slug to a user. Safe on ``None``/blank (returns ``""``).

    Usage: {{ tag|humanize_tag }}
    """
    if value is None:
        return ''
    slug = str(value).strip()
    if not slug:
        return ''
    override = _TAG_LABEL_OVERRIDES.get(slug.lower())
    if override:
        return override
    return slug.replace('-', ' ').replace('_', ' ').title()


@register.filter
def has_common(seq_a, seq_b):
    """Return True if the two sequences share at least one element.

    Used to auto-open the "More topics" disclosure on listing pages only
    when an active tag filter lives in the hidden long-tail (issue #1319),
    so the active pill is never rendered invisible.

    Usage: {% if selected_tags|has_common:hidden_tags %}open{% endif %}
    """
    if not seq_a or not seq_b:
        return False
    return bool(set(seq_a) & set(seq_b))


def _bounded_tags(selected_tags, max_tags):
    """Normalize/deduplicate tags only for an explicitly bounded caller."""
    tags = []
    for raw_tag in selected_tags or ():
        tag = str(raw_tag or '').strip()
        if not tag or tag in tags:
            continue
        tags.append(tag)
        if len(tags) == max_tags:
            break
    return tags


@register.simple_tag
def tag_add_url(base_path, selected_tags, tag, extra_params=None, max_tags=None):
    """Build URL that adds a tag to the current selection.

    Usage: {% tag_add_url base_path selected_tags "python" %}
    """
    tags = (
        _bounded_tags(selected_tags, int(max_tags))
        if max_tags is not None
        else list(selected_tags) if selected_tags else []
    )
    if tag not in tags and (max_tags is None or len(tags) < int(max_tags)):
        tags.append(tag)
    return _build_url(base_path, tags, extra_params)


@register.simple_tag
def tag_remove_url(base_path, selected_tags, tag, extra_params=None, max_tags=None):
    """Build URL that removes a tag from the current selection.

    Usage: {% tag_remove_url base_path selected_tags "python" %}
    """
    tags = (
        _bounded_tags(selected_tags, int(max_tags))
        if max_tags is not None
        else list(selected_tags or [])
    )
    tags = [t for t in tags if t != tag]
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
