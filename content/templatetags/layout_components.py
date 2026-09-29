"""Layout components that can only render the design-system reading order.

``_docs/design-system.md`` (Spacing and Layout) requires a section's title to
come first, with its discovery link or action group below it and wrapping
rather than pinned opposite it, and requires module focus, progress, and the
next action to stay in one vertical reading order.  These tags own those two
recurring patterns so a template cannot hand-roll a ``justify-between`` row.

The static guard in ``content/tests/test_design_layout_lint.py`` flags the
hand-rolled versions, and ``playwright_tests/test_design_layout_guard.py``
checks the rendered geometry.
"""

from django import template

register = template.Library()

SECTION_HEADER_LEVELS = frozenset({2, 3})
SECTION_HEADER_SIZES = frozenset({'md', 'lg'})


@register.inclusion_tag('includes/_section_header.html')
def section_header(
    title,
    level=2,
    size='md',
    heading_id='',
    subtitle='',
    see_all_url='',
    see_all_label='',
    testid='',
    heading_testid='',
    see_all_testid='',
    extra='',
):
    """Render a section title with an optional discovery link below it.

    ``size`` picks the documented heading role: ``md`` is the compact
    ``text-lg`` section title used inside member hubs, ``lg`` is the
    ``text-xl`` tab/dashboard section title.  ``extra`` appends wrapper
    classes such as the spacing to the following content (``mb-3``).
    """
    level = int(level)
    if level not in SECTION_HEADER_LEVELS:
        raise template.TemplateSyntaxError(f'section_header level must be 2 or 3, got {level!r}')
    if size not in SECTION_HEADER_SIZES:
        raise template.TemplateSyntaxError(f'section_header size must be md or lg, got {size!r}')
    if bool(see_all_url) != bool(see_all_label):
        raise template.TemplateSyntaxError('section_header needs both see_all_url and see_all_label, or neither')
    return {
        'title': title,
        'level': level,
        'size': size,
        'heading_id': heading_id,
        'subtitle': subtitle,
        'see_all_url': see_all_url,
        'see_all_label': see_all_label,
        'testid': testid,
        'heading_testid': heading_testid,
        'see_all_testid': see_all_testid,
        'extra': extra,
    }


def _percent(done, total):
    if not total:
        return 0
    return max(0, min(100, int(done * 100 / total)))


@register.inclusion_tag('includes/_progress_block.html')
def progress_block(
    done,
    total,
    label='Progress',
    title='',
    summary_word='done',
    breakdown='',
    action_url='',
    action_label='',
    testid='',
    summary_testid='',
    breakdown_testid='',
    action_testid='',
):
    """Render progress top to bottom: title, bar, count, breakdown, action.

    The optional primary action always sits below the progress text, never
    beside the bar.  ``label`` is the progressbar's accessible name.
    """
    done = int(done or 0)
    total = int(total or 0)
    if bool(action_url) != bool(action_label):
        raise template.TemplateSyntaxError('progress_block needs both action_url and action_label, or neither')
    return {
        'done': done,
        'total': total,
        'percent': _percent(done, total),
        'label': label,
        'title': title,
        'summary_word': summary_word,
        'breakdown': breakdown,
        'action_url': action_url,
        'action_label': action_label,
        'testid': testid,
        'summary_testid': summary_testid,
        'breakdown_testid': breakdown_testid,
        'action_testid': action_testid,
    }
