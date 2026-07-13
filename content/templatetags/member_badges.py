"""Member/public badge component tags."""

from django import template

from content.access import get_required_tier_label

register = template.Library()

BASE_CLASSES = 'inline-flex max-w-full shrink-0 items-center rounded-full font-medium'

SIZE_CLASSES = {
    'xs': 'gap-1 px-2.5 py-0.5 text-xs',
    'sm': 'gap-1 px-3 py-1 text-xs',
    'md': 'gap-2 px-4 py-1.5 text-sm',
}

ICON_SIZE_CLASSES = {
    'xs': 'h-3 w-3',
    'sm': 'h-3 w-3',
    'md': 'h-4 w-4',
}

TONE_CLASSES = {
    'neutral': 'bg-secondary text-muted-foreground',
    'muted': 'bg-secondary text-muted-foreground',
    'accent': 'bg-accent/10 text-accent',
    'accent_strong': 'bg-accent/20 text-accent',
    'accent_outline': 'border border-accent/30 bg-accent/10 text-accent',
    'accent_outline_strong': 'border border-accent/40 bg-accent/10 text-accent',
    'success': 'bg-green-500/20 text-green-400',
    'success_soft': 'bg-green-500/15 text-green-400',
    'info': 'bg-blue-500/20 text-blue-400',
    'danger': 'bg-red-500/20 text-red-400',
    'purple': 'bg-purple-500/20 text-purple-400',
}

STATUS_TONES = {
    'active': 'success',
    'closed': 'danger',
    'enrolled': 'success_soft',
    'open': 'success',
    'past': 'muted',
    'proposals_open': 'success',
    'registered': 'success',
    'upcoming': 'info',
}


def _normalize(value):
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_')


def _context(
    label,
    *,
    tone='neutral',
    size='xs',
    icon='',
    element_id='',
    testid='',
    extra_class='',
    uppercase=False,
    required_level=None,
):
    size_classes = SIZE_CLASSES.get(size, SIZE_CLASSES['xs'])
    tone_classes = TONE_CLASSES.get(tone, TONE_CLASSES['neutral'])
    classes = f'{BASE_CLASSES} {size_classes} {tone_classes}'
    if uppercase:
        classes = f'{classes} uppercase tracking-wide'
    if extra_class:
        classes = f'{classes} {extra_class}'
    return {
        'label': label,
        'icon': icon,
        'element_id': element_id,
        'testid': testid,
        'classes': classes,
        'icon_classes': ICON_SIZE_CLASSES.get(size, ICON_SIZE_CLASSES['xs']),
        'has_required_level': required_level is not None,
        'required_level': required_level,
    }


@register.inclusion_tag('includes/member_badge.html')
def member_badge(
    label,
    tone='neutral',
    size='xs',
    icon='',
    element_id='',
    testid='',
    extra_class='',
    uppercase=False,
):
    """Render a shared member/public badge."""
    return _context(
        label,
        tone=tone,
        size=size,
        icon=icon,
        element_id=element_id,
        testid=testid,
        extra_class=extra_class,
        uppercase=uppercase,
    )


@register.inclusion_tag('includes/member_badge.html')
def member_tier_badge(
    required_level,
    tone='accent',
    size='xs',
    icon='',
    element_id='',
    testid='',
    extra_class='',
):
    """Render a required-tier badge using the public access vocabulary."""
    return _context(
        get_required_tier_label(required_level),
        tone=tone,
        size=size,
        icon=icon,
        element_id=element_id,
        testid=testid,
        extra_class=extra_class,
        required_level=required_level,
    )


@register.inclusion_tag('includes/member_badge.html')
def member_status_badge(
    label,
    status='',
    tone='',
    size='xs',
    icon='',
    element_id='',
    testid='',
    extra_class='',
    uppercase=False,
):
    """Render a visible-text status badge for member/public surfaces."""
    status_key = _normalize(status or label)
    resolved_tone = tone or STATUS_TONES.get(status_key, 'muted')
    return _context(
        label,
        tone=resolved_tone,
        size=size,
        icon=icon,
        element_id=element_id,
        testid=testid,
        extra_class=extra_class,
        uppercase=uppercase,
    )


@register.inclusion_tag('includes/member_badge.html')
def member_label_badge(
    label,
    tone='accent',
    size='xs',
    icon='',
    element_id='',
    testid='',
    extra_class='',
):
    """Render a simple member-facing label badge."""
    return _context(
        label,
        tone=tone,
        size=size,
        icon=icon,
        element_id=element_id,
        testid=testid,
        extra_class=extra_class,
    )
