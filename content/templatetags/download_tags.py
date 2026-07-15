"""
Template tags for rendering download shortcodes in content.

The ``{{download:slug}}`` shortcode in markdown/HTML content is processed
by the ``render_download_shortcodes`` filter, which replaces each shortcode
with an inline download CTA card.

Usage in templates:
    {% load download_tags %}
    {{ article.content_html|render_download_shortcodes:request|safe }}
"""

import re

from django import template
from django.template.loader import render_to_string

register = template.Library()

# Pattern matches {{download:some-slug}} with optional whitespace
DOWNLOAD_SHORTCODE_RE = re.compile(r'\{\{\s*download\s*:\s*([\w-]+)\s*\}\}')


@register.filter
def render_download_shortcodes(html_content, request):
    """Replace {{download:slug}} shortcodes with inline download cards.

    Args:
        html_content: The rendered HTML content string.
        request: The current HttpRequest (passed as the filter argument).

    Returns:
        HTML with shortcodes replaced by rendered download card templates.
    """
    if not html_content:
        return html_content

    # Avoid circular import
    from content.models import Download

    def replace_shortcode(match):
        slug = match.group(1)
        try:
            download = Download.objects.get(slug=slug, published=True)
        except Download.DoesNotExist:
            # Public prose must not leak stale/typoed download slugs.
            return ''

        from content.access import (
            can_access,
            get_gated_reason,
            get_required_tier_name,
        )

        has_access = can_access(request.user, download)
        gated_reason = get_gated_reason(request.user, download)
        if download.required_level == 0 and not request.user.is_authenticated:
            cta_label = 'Get free download'
        elif gated_reason == 'unverified_email':
            cta_label = 'Verify email to download'
        elif has_access:
            cta_label = 'Download'
        else:
            cta_label = f'View {get_required_tier_name(download.required_level)} access'

        context = {
            'download': download,
            'has_access': has_access,
            'gated_reason': gated_reason,
            'required_tier_name': get_required_tier_name(
                download.required_level,
            ),
            'shortcode_cta_label': cta_label,
        }
        return render_to_string('includes/download_card.html', context)

    return DOWNLOAD_SHORTCODE_RE.sub(replace_shortcode, html_content)
