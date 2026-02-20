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

from content.access import can_access, get_required_tier_name

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
            # Leave the shortcode as-is if the download doesn't exist
            return match.group(0)

        has_access = can_access(request.user, download)
        is_lead_magnet = download.required_level == 0
        is_anonymous = not request.user.is_authenticated

        context = {
            'download': download,
            'has_access': has_access,
            'show_email_form': is_lead_magnet and is_anonymous,
            'cta_message': (
                f'Upgrade to {get_required_tier_name(download.required_level)} to download'
                if not has_access and not is_lead_magnet else ''
            ),
        }
        return render_to_string('includes/download_card.html', context)

    return DOWNLOAD_SHORTCODE_RE.sub(replace_shortcode, html_content)
