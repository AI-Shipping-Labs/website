"""
Template tags for SEO: structured data (JSON-LD) and OpenGraph/Twitter meta tags.

Usage in templates:
    {% load seo_tags %}

    {# Generate JSON-LD structured data for a content object #}
    {% structured_data article %}

    {# Generate OpenGraph and Twitter Card meta tags #}
    {% og_tags article %}
"""

import json

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


SITE_NAME = 'AI Shipping Labs'


def _get_site_url():
    """Return the site URL from settings."""
    return getattr(settings, 'SITE_URL', 'https://aishippinglabs.com')


def _truncate_description(text, max_length=160):
    """Truncate description to max_length characters."""
    if not text:
        return ''
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length - 3].rsplit(' ', 1)[0] + '...'


def _get_content_type(obj):
    """Determine the content type from the model class name."""
    class_name = obj.__class__.__name__
    return class_name.lower()


def _build_article_jsonld(article):
    """Build JSON-LD for an Article (blog post)."""
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'Article',
        'headline': article.title,
        'description': _truncate_description(
            getattr(article, 'description', ''),
        ),
        'datePublished': _format_date(article),
        'dateModified': _format_datetime(
            getattr(article, 'updated_at', None),
        ),
        'author': {
            '@type': 'Person',
            'name': getattr(article, 'author', '') or 'AI Shipping Labs',
        },
        'publisher': {
            '@type': 'Organization',
            'name': SITE_NAME,
            'url': site_url,
        },
        'mainEntityOfPage': {
            '@type': 'WebPage',
            '@id': f'{site_url}{article.get_absolute_url()}',
        },
    }
    if getattr(article, 'cover_image_url', ''):
        data['image'] = article.cover_image_url
    return data


def _build_course_jsonld(course):
    """Build JSON-LD for a Course."""
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'Course',
        'name': course.title,
        'description': _truncate_description(
            getattr(course, 'description', ''),
        ),
        'provider': {
            '@type': 'Organization',
            'name': SITE_NAME,
            'url': site_url,
        },
        'url': f'{site_url}{course.get_absolute_url()}',
    }
    if getattr(course, 'is_free', False):
        data['offers'] = {
            '@type': 'Offer',
            'price': '0',
            'priceCurrency': 'EUR',
        }
    if getattr(course, 'cover_image_url', ''):
        data['image'] = course.cover_image_url
    return data


def _build_recording_jsonld(recording):
    """Build JSON-LD for a Recording (VideoObject or LearningResource)."""
    site_url = _get_site_url()
    video_url = getattr(recording, 'youtube_url', '') or getattr(
        recording, 'google_embed_url', '',
    )

    if video_url:
        data = {
            '@context': 'https://schema.org',
            '@type': 'VideoObject',
            'name': recording.title,
            'description': _truncate_description(
                getattr(recording, 'description', ''),
            ),
            'embedUrl': video_url,
            'uploadDate': _format_date(recording),
            'url': f'{site_url}{recording.get_absolute_url()}',
        }
    else:
        data = {
            '@context': 'https://schema.org',
            '@type': 'LearningResource',
            'name': recording.title,
            'description': _truncate_description(
                getattr(recording, 'description', ''),
            ),
            'url': f'{site_url}{recording.get_absolute_url()}',
        }
    return data


def _build_event_jsonld(event):
    """Build JSON-LD for an Event."""
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'Event',
        'name': event.title,
        'description': _truncate_description(
            getattr(event, 'description', ''),
        ),
        'startDate': _format_datetime(
            getattr(event, 'start_datetime', None),
        ),
        'organizer': {
            '@type': 'Organization',
            'name': SITE_NAME,
            'url': site_url,
        },
        'url': f'{site_url}{event.get_absolute_url()}',
    }
    if getattr(event, 'end_datetime', None):
        data['endDate'] = _format_datetime(event.end_datetime)
    location = getattr(event, 'location', '')
    if location:
        data['location'] = {
            '@type': 'VirtualLocation',
            'name': location,
        }
    else:
        data['location'] = {
            '@type': 'VirtualLocation',
            'name': 'Online',
        }
    return data


def _build_unit_jsonld(unit):
    """Build JSON-LD for a course Unit (LearningResource)."""
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'LearningResource',
        'name': unit.title,
        'url': f'{site_url}{unit.get_absolute_url()}',
    }
    if getattr(unit, 'video_url', ''):
        data['video'] = {
            '@type': 'VideoObject',
            'embedUrl': unit.video_url,
        }
    return data


def _build_organization_jsonld():
    """Build JSON-LD for the Organization (homepage)."""
    site_url = _get_site_url()
    site_description = getattr(settings, 'SITE_DESCRIPTION', '')
    return {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        'name': SITE_NAME,
        'url': site_url,
        'description': site_description,
        'founder': {
            '@type': 'Person',
            'name': 'Alexey Grigorev',
        },
    }


def _format_date(obj):
    """Format a date field from an object. Handles both date and datetime."""
    date_val = getattr(obj, 'date', None)
    if date_val:
        return date_val.isoformat()
    published_at = getattr(obj, 'published_at', None)
    if published_at:
        return published_at.isoformat()
    created_at = getattr(obj, 'created_at', None)
    if created_at:
        return created_at.isoformat()
    return ''


def _format_datetime(dt):
    """Format a datetime to ISO format string."""
    if dt:
        return dt.isoformat()
    return ''


JSONLD_BUILDERS = {
    'article': _build_article_jsonld,
    'course': _build_course_jsonld,
    'recording': _build_recording_jsonld,
    'event': _build_event_jsonld,
    'unit': _build_unit_jsonld,
    'project': _build_article_jsonld,  # Projects use Article schema
    'tutorial': _build_article_jsonld,  # Tutorials use Article schema
}


@register.simple_tag
def structured_data(content=None):
    """Generate JSON-LD structured data script tag for a content object.

    Usage:
        {% structured_data article %}
        {% structured_data %}  {# for homepage Organization #}
    """
    if content is None:
        data = _build_organization_jsonld()
    else:
        content_type = _get_content_type(content)
        builder = JSONLD_BUILDERS.get(content_type)
        if builder:
            data = builder(content)
        else:
            data = _build_organization_jsonld()

    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    return mark_safe(
        f'<script type="application/ld+json">\n{json_str}\n</script>',
    )


def _get_og_type(obj):
    """Determine the OpenGraph type for a content object."""
    content_type = _get_content_type(obj)
    if content_type == 'event':
        return 'event'
    if content_type in ('article', 'project', 'tutorial'):
        return 'article'
    return 'website'


def _get_image_url(obj):
    """Get the best image URL for a content object."""
    for attr in ('cover_image_url',):
        url = getattr(obj, attr, '')
        if url:
            return url
    return ''


@register.simple_tag(takes_context=True)
def og_tags(context, content=None):
    """Generate OpenGraph and Twitter Card meta tags.

    Usage:
        {% og_tags article %}
        {% og_tags %}  {# for homepage #}
    """
    request = context.get('request')
    site_url = _get_site_url()

    if content is None:
        title = f'{SITE_NAME} | A Technical Community'
        description = getattr(settings, 'SITE_DESCRIPTION', '')
        og_type = 'website'
        canonical_url = site_url
        image_url = ''
    else:
        title = getattr(content, 'title', SITE_NAME)
        description = _truncate_description(
            getattr(content, 'description', ''),
        )
        og_type = _get_og_type(content)
        canonical_url = f'{site_url}{content.get_absolute_url()}'
        image_url = _get_image_url(content)

    tags = [
        f'<meta property="og:title" content="{_escape_attr(title)}">',
        f'<meta property="og:description" content="{_escape_attr(description)}">',
        f'<meta property="og:url" content="{canonical_url}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
    ]
    if image_url:
        tags.append(f'<meta property="og:image" content="{_escape_attr(image_url)}">')

    # Twitter Card tags
    tags.extend([
        f'<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_escape_attr(title)}">',
        f'<meta name="twitter:description" content="{_escape_attr(description)}">',
    ])
    if image_url:
        tags.append(f'<meta name="twitter:image" content="{_escape_attr(image_url)}">')

    return mark_safe('\n  '.join(tags))


def _escape_attr(value):
    """Escape HTML attribute value."""
    if not value:
        return ''
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('"', '&quot;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )
