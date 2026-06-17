"""Member-facing empty-state component tags."""

from django import template

register = template.Library()

DEFAULT_CTA_CLASS = 'text-accent hover:underline'


@register.inclusion_tag('includes/member_empty_state.html')
def member_empty_state(
    title,
    body,
    icon='inbox',
    kind='fresh',
    primary_cta_label='',
    primary_cta_url='',
    primary_cta_icon='',
    primary_cta_class='',
    secondary_cta_label='',
    secondary_cta_url='',
    secondary_cta_icon='',
    secondary_cta_class='',
    testid='',
):
    """Render the shared member/public empty-state card.

    ``kind`` is ``"fresh"`` when no content exists yet and ``"filter"``
    when active filters produced no matching rows. CTAs are optional and
    caller-styled so authenticated surfaces can pass ``button_classes``
    while public catalog pages can keep text-link CTAs.
    """
    return {
        'title': title,
        'body': body,
        'icon': icon,
        'kind': kind,
        'primary_cta_label': primary_cta_label,
        'primary_cta_url': primary_cta_url,
        'primary_cta_icon': primary_cta_icon,
        'primary_cta_class': primary_cta_class or DEFAULT_CTA_CLASS,
        'secondary_cta_label': secondary_cta_label,
        'secondary_cta_url': secondary_cta_url,
        'secondary_cta_icon': secondary_cta_icon,
        'secondary_cta_class': secondary_cta_class or DEFAULT_CTA_CLASS,
        'testid': testid,
    }
