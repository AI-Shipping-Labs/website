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
import re

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

from content.templatetags.teaser_tags import strip_markdown
from content.utils.h1 import strip_leading_title_h1
from events.services.display_time import format_event_tz_strip
from integrations.config import site_base_url
from integrations.services.banner_generator.resolve import effective_banner_url

register = template.Library()


SITE_NAME = 'AI Shipping Labs'
DEFAULT_OG_IMAGE_PATH = '/static/ai-shipping-labs.jpg'
DEFAULT_OG_IMAGE_ALT = 'AI Shipping Labs'
_FENCED_CODE_BLOCK_RE = re.compile(r'(?ms)(```.*?```|~~~.*?~~~)')
_MARKDOWN_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_HTML_COMMENT_RE = re.compile(r'(?s)<!--.*?-->')
_WHITESPACE_RE = re.compile(r'\s+')

CONTENT_TYPE_LABELS = {
    'article': 'article',
    'course': 'course',
    'event': 'event',
    'module': 'course module',
    'project': 'project',
    'recording': 'recording',
    'tutorial': 'tutorial',
    'unit': 'course unit',
    'workshop': 'workshop',
    'workshop_page': 'workshop tutorial page',
    'workshoppage': 'workshop tutorial page',
    'workshop_video': 'workshop recording',
}


def _get_site_url():
    """Return the site URL, honoring the Studio DB override."""
    return site_base_url()


def _truncate_description(text, max_length=160):
    """Truncate description to max_length characters."""
    if not text:
        return ''
    if max_length <= 3:
        return text[:max_length]
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length - 3].rsplit(' ', 1)[0] + '...'


def _get_content_type(obj):
    """Determine the content type from the model class name."""
    class_name = obj.__class__.__name__
    return class_name.lower()


def _resolve_content_type(obj, content_type=None):
    """Return the explicit SEO content type, or infer it from the object."""
    if content_type:
        return content_type
    return _get_content_type(obj)


def _clean_seo_source(value):
    """Return plain text suitable for metadata descriptions.

    The normal teaser ``strip_markdown`` filter intentionally preserves code
    text. SEO snippets should lead with prose, so fenced code blocks and image
    syntax are removed before markdown is rendered to plain text.
    """
    if not value:
        return ''
    text = str(value)
    text = _HTML_COMMENT_RE.sub(' ', text)
    text = _FENCED_CODE_BLOCK_RE.sub(' ', text)
    text = _MARKDOWN_IMAGE_RE.sub(' ', text)
    return _WHITESPACE_RE.sub(' ', strip_markdown(text)).strip()


def _fallback_description_from_title(title, content_type):
    """Build a content-specific fallback instead of using the site default."""
    label = CONTENT_TYPE_LABELS.get(content_type, 'page')
    if title:
        return f'Explore {title}, an AI Shipping Labs {label}.'
    return f'Explore this AI Shipping Labs {label}.'


def _content_specific_fallback(obj, content_type):
    """Build a non-empty fallback for the object/content-type pair."""
    title = getattr(obj, 'title', '')

    if content_type in ('workshop_page', 'workshoppage'):
        workshop_title = getattr(getattr(obj, 'workshop', None), 'title', '')
        if title and workshop_title:
            return f'{title} is a workshop tutorial page for {workshop_title}.'

    if content_type == 'workshop_video':
        if title:
            return f'Watch the recording for {title}, an AI Shipping Labs workshop.'

    if content_type == 'module':
        course_title = getattr(getattr(obj, 'course', None), 'title', '')
        if title and course_title:
            return f'{title} is a course module in {course_title}.'

    if content_type == 'unit':
        module = getattr(obj, 'module', None)
        course_title = getattr(getattr(module, 'course', None), 'title', '')
        if title and course_title:
            return f'{title} is a course unit in {course_title}.'

    return _fallback_description_from_title(title, content_type)


def _body_source_without_duplicate_h1(obj, attr, title_attr='title'):
    """Return markdown body text with a duplicate leading H1 removed."""
    source = getattr(obj, attr, '') or ''
    title = getattr(obj, title_attr, '') or ''
    if source and title:
        return strip_leading_title_h1(source, title)
    return source


def _description_source(obj, content_type):
    """Choose the best source text for a content object's SEO description."""
    explicit_description = getattr(obj, 'description', '') or ''
    if explicit_description:
        return explicit_description

    if content_type in ('article', 'project', 'tutorial'):
        return _body_source_without_duplicate_h1(obj, 'content_markdown')
    if content_type == 'module':
        return _body_source_without_duplicate_h1(obj, 'overview')
    if content_type == 'unit':
        return _body_source_without_duplicate_h1(obj, 'body')
    if content_type in ('workshop_page', 'workshoppage'):
        return _body_source_without_duplicate_h1(obj, 'body')
    if content_type == 'workshop_video':
        return getattr(obj, 'description', '') or ''

    return ''


def _description_with_context(obj, content_type, description):
    """Add parent/page context for child pages that otherwise duplicate snippets."""
    if not description:
        return _content_specific_fallback(obj, content_type)

    title = getattr(obj, 'title', '')
    if content_type in ('workshop_page', 'workshoppage'):
        workshop_title = getattr(getattr(obj, 'workshop', None), 'title', '')
        if title and workshop_title:
            return f'{title} in {workshop_title}: {description}'
    if content_type == 'workshop_video' and title:
        return f'Recording for {title}: {description}'
    if content_type == 'module':
        course_title = getattr(getattr(obj, 'course', None), 'title', '')
        if title and course_title:
            return f'{title} in {course_title}: {description}'
    if content_type == 'unit':
        module = getattr(obj, 'module', None)
        course_title = getattr(getattr(module, 'course', None), 'title', '')
        if title and course_title:
            return f'{title} in {course_title}: {description}'
    return description


def build_seo_description(content, content_type=None, max_length=160):
    """Build a cleaned, content-specific SEO description string."""
    resolved_type = _resolve_content_type(content, content_type)
    if resolved_type == 'event':
        return _event_preview_description(content)

    source = _description_source(content, resolved_type)
    description = _clean_seo_source(source)
    description = _description_with_context(content, resolved_type, description)
    return _truncate_description(description, max_length=max_length)


@register.simple_tag
def seo_description(content, content_type=None, max_length=160):
    """Template tag wrapper for ``build_seo_description``."""
    return build_seo_description(content, content_type, max_length=max_length)


def _seo_title(content, content_type):
    """Return the metadata/social title for a content object."""
    title = getattr(content, 'title', SITE_NAME)
    if content_type in ('workshop_page', 'workshoppage'):
        workshop_title = getattr(getattr(content, 'workshop', None), 'title', '')
        if workshop_title:
            return f'{title} | {workshop_title}'
    if content_type == 'workshop_video':
        return f'{title} - Recording'
    return title


def _canonical_path(content, content_type):
    """Return the canonical path for a content object or variant page."""
    if content_type == 'workshop_video':
        return f'{content.get_absolute_url()}/video'
    return content.get_absolute_url()


def _image_source(content, content_type):
    """Return the record whose banner fields should feed OG/Twitter images."""
    if content_type in ('workshop_page', 'workshoppage'):
        return getattr(content, 'workshop', content)
    return content


# Issue #817: event link previews lead with the same multi-timezone time strip
# used in Slack announcements. The combined preview is capped at 200 chars
# (wider than the default 160) so the high-value date/time is never truncated.
_EVENT_PREVIEW_MAX_LENGTH = 200


def _event_preview_description(event):
    """Build the event link-preview description: ``<time-strip> · <description>``.

    The time strip leads and is never truncated; only the description portion
    is cut at a word boundary with a ``...`` ellipsis so the combined string
    fits ``_EVENT_PREVIEW_MAX_LENGTH``. Falls back to the plain truncated
    description when ``start_datetime`` is missing (defensive guard).
    """
    time_strip = format_event_tz_strip(getattr(event, 'start_datetime', None))
    description = _clean_seo_source(getattr(event, 'description', '') or '')

    if not time_strip:
        if not description:
            description = _content_specific_fallback(event, 'event')
        return _truncate_description(description)

    if not description:
        return time_strip

    separator = ' · '
    available = _EVENT_PREVIEW_MAX_LENGTH - len(time_strip) - len(separator)
    truncated_description = _truncate_description(description, max_length=available)
    return f'{time_strip}{separator}{truncated_description}'


def _build_article_jsonld(article):
    """Build JSON-LD for an Article (blog post)."""
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'Article',
        'headline': article.title,
        'description': build_seo_description(article),
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
        'description': build_seo_description(course, 'course'),
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


def _build_workshop_jsonld(workshop):
    """Build JSON-LD for a Workshop (Course schema).

    Workshops are structured learning artefacts with a recording, a code
    repo, and ordered pages — ``Course`` is the closest schema.org type.
    The ``hasCourseInstance`` block carries the workshop date so search
    engines can show it as a dated learning event.
    """
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'Course',
        'name': workshop.title,
        'description': build_seo_description(workshop, 'workshop'),
        'provider': {
            '@type': 'Organization',
            'name': SITE_NAME,
            'url': site_url,
        },
        'url': f'{site_url}{workshop.get_absolute_url()}',
    }
    if getattr(workshop, 'cover_image_url', ''):
        data['image'] = workshop.cover_image_url
    workshop_date = getattr(workshop, 'date', None)
    if workshop_date:
        data['hasCourseInstance'] = {
            '@type': 'CourseInstance',
            'courseMode': 'online',
            'startDate': workshop_date.isoformat(),
        }
    return data


def _build_recording_jsonld(event):
    """Build JSON-LD for a recording (Event with recording, VideoObject or LearningResource).

    The canonical ``url`` is the unified event detail page (``/events/<slug>``)
    since recordings are no longer served on a separate surface.
    """
    site_url = _get_site_url()
    video_url = getattr(event, 'recording_url', '') or getattr(
        event, 'recording_embed_url', '',
    )
    page_url = event.get_absolute_url()

    if video_url:
        data = {
            '@context': 'https://schema.org',
            '@type': 'VideoObject',
            'name': event.title,
            'description': build_seo_description(event, 'recording'),
            'embedUrl': video_url,
            'uploadDate': _format_date(event),
            'url': f'{site_url}{page_url}',
        }
    else:
        data = {
            '@context': 'https://schema.org',
            '@type': 'LearningResource',
            'name': event.title,
            'description': build_seo_description(event, 'recording'),
            'url': f'{site_url}{page_url}',
        }
    return data


def _build_event_jsonld(event):
    """Build JSON-LD for an Event."""
    site_url = _get_site_url()
    data = {
        '@context': 'https://schema.org',
        '@type': 'Event',
        'name': event.title,
        'description': build_seo_description(event, 'event'),
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
        'description': build_seo_description(unit, 'unit'),
        'url': f'{site_url}{unit.get_absolute_url()}',
    }
    if getattr(unit, 'video_url', ''):
        data['video'] = {
            '@type': 'VideoObject',
            'embedUrl': unit.video_url,
        }
    return data


def _build_workshop_page_jsonld(page):
    """Build JSON-LD for a workshop tutorial page."""
    site_url = _get_site_url()
    return {
        '@context': 'https://schema.org',
        '@type': 'Article',
        'headline': page.title,
        'description': build_seo_description(page, 'workshop_page'),
        'publisher': {
            '@type': 'Organization',
            'name': SITE_NAME,
            'url': site_url,
        },
        'mainEntityOfPage': {
            '@type': 'WebPage',
            '@id': f'{site_url}{page.get_absolute_url()}',
        },
    }


def _build_workshop_video_jsonld(workshop):
    """Build JSON-LD for a workshop recording page."""
    site_url = _get_site_url()
    page_url = f'{workshop.get_absolute_url()}/video'
    event = getattr(workshop, 'event', None)
    video_url = ''
    upload_date = ''
    if event is not None:
        video_url = (
            getattr(event, 'recording_url', '')
            or getattr(event, 'recording_embed_url', '')
        )
        upload_date = _format_date(event)

    data = {
        '@context': 'https://schema.org',
        '@type': 'VideoObject' if video_url else 'LearningResource',
        'name': _seo_title(workshop, 'workshop_video'),
        'description': build_seo_description(workshop, 'workshop_video'),
        'url': f'{site_url}{page_url}',
    }
    if video_url:
        data['embedUrl'] = video_url
    if upload_date:
        data['uploadDate'] = upload_date
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
        'sameAs': [
            'https://twitter.com/Al_Grigor',
            'https://github.com/AI-Shipping-Labs',
        ],
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


def build_faqpage_jsonld(sections):
    """Build FAQPage JSON-LD from interview sections with Q&A items.

    Each section should have a 'qa' list of dicts with 'question' and 'answer' keys.
    If an item has no 'answer', the question text is used as the answer.
    """
    questions = []
    for section in sections:
        for item in section.get('qa', []):
            question_text = item.get('question', '')
            answer_text = item.get('answer', '') or question_text
            if question_text:
                questions.append({
                    '@type': 'Question',
                    'name': question_text,
                    'acceptedAnswer': {
                        '@type': 'Answer',
                        'text': answer_text,
                    },
                })
    return {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': questions,
    }


def build_course_learning_path_jsonld(title, description, learning_stages):
    """Build Course JSON-LD for a learning path page.

    learning_stages is a list of dicts with 'title' and optional 'items'.
    """
    site_url = _get_site_url()
    cleaned_description = _clean_seo_source(description)
    if not cleaned_description:
        cleaned_description = _fallback_description_from_title(title, 'course')
    data = {
        '@context': 'https://schema.org',
        '@type': 'Course',
        'name': title,
        'description': _truncate_description(cleaned_description),
        'provider': {
            '@type': 'Organization',
            'name': SITE_NAME,
            'url': site_url,
        },
        'url': f'{site_url}/learning-path/ai-engineer',
    }
    if learning_stages:
        parts = []
        for stage in learning_stages:
            part = {
                '@type': 'CourseInstance',
                'name': stage.get('title', ''),
            }
            items = stage.get('items', [])
            if items:
                part['description'] = '; '.join(items)
            parts.append(part)
        data['hasPart'] = parts
    return data


JSONLD_BUILDERS = {
    'article': _build_article_jsonld,
    'course': _build_course_jsonld,
    'recording': _build_recording_jsonld,
    'event': _build_event_jsonld,
    'unit': _build_unit_jsonld,
    'project': _build_article_jsonld,  # Projects use Article schema
    'tutorial': _build_article_jsonld,  # Tutorials use Article schema
    'workshop': _build_workshop_jsonld,
    'workshop_page': _build_workshop_page_jsonld,
    'workshoppage': _build_workshop_page_jsonld,
    'workshop_video': _build_workshop_video_jsonld,
}


@register.simple_tag
def structured_data(content=None, content_type=None):
    """Generate JSON-LD structured data script tag for a content object.

    Usage:
        {% structured_data article %}
        {% structured_data recording "recording" %}
        {% structured_data %}  {# for homepage Organization #}
    """
    if content is None:
        data = _build_organization_jsonld()
    else:
        if content_type is None:
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


def _get_og_type(obj, content_type=None):
    """Determine the OpenGraph type for a content object."""
    content_type = _resolve_content_type(obj, content_type)
    if content_type == 'event':
        return 'event'
    if content_type in (
        'article',
        'project',
        'tutorial',
        'workshop',
        'workshop_page',
        'workshoppage',
        'workshop_video',
    ):
        return 'article'
    return 'website'


def _get_image_url(obj):
    """Get the best image URL for a content object.

    Resolves the public OG/Twitter preview image with the issue #931
    precedence: frontmatter ``cover_image_url`` wins, then the
    operator-uploaded ``custom_banner_url`` (sync-safe Studio upload), then
    the platform-generated ``auto_banner_url``. Delegated to
    :func:`integrations.services.banner_generator.resolve.effective_banner_url`
    so the public meta tags, every model ``display_image_url`` accessor,
    and the Studio preview panel stay in lockstep.
    """
    return effective_banner_url(obj)


@register.simple_tag(takes_context=True)
def og_tags(context, content=None, content_type=None):
    """Generate OpenGraph and Twitter Card meta tags.

    Usage:
        {% og_tags article %}
        {% og_tags %}  {# for homepage #}
    """
    context.get('request')
    site_url = _get_site_url()

    if content is None:
        title = f'{SITE_NAME} | A Technical Community'
        description = getattr(settings, 'SITE_DESCRIPTION', '')
        og_type = 'website'
        canonical_url = site_url
        image_url = ''
    else:
        resolved_type = _resolve_content_type(content, content_type)
        title = _seo_title(content, resolved_type)
        description = build_seo_description(content, resolved_type)
        og_type = _get_og_type(content, resolved_type)
        canonical_url = f'{site_url}{_canonical_path(content, resolved_type)}'
        image_url = _get_image_url(_image_source(content, resolved_type))

    # Use default OG image as fallback when no content-specific image
    default_image_url = f'{site_url}{DEFAULT_OG_IMAGE_PATH}'
    effective_image_url = image_url or default_image_url
    image_alt = _escape_attr(title) if image_url else DEFAULT_OG_IMAGE_ALT

    tags = [
        f'<meta property="og:title" content="{_escape_attr(title)}">',
        f'<meta property="og:description" content="{_escape_attr(description)}">',
        f'<meta property="og:url" content="{canonical_url}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:image" content="{_escape_attr(effective_image_url)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        f'<meta property="og:image:alt" content="{image_alt}">',
    ]

    # Twitter Card tags
    tags.extend([
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_escape_attr(title)}">',
        f'<meta name="twitter:description" content="{_escape_attr(description)}">',
        f'<meta name="twitter:image" content="{_escape_attr(effective_image_url)}">',
        '<meta name="twitter:creator" content="@Al_Grigor">',
    ])

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


@register.simple_tag
def event_preview_description(event):
    """Return the event link-preview description string.

    Exposes the same combined ``<time-strip> · <description>`` string used for
    the event ``og:description``/``twitter:description`` so the page's
    ``<meta name="description">`` renders identically.

    Usage:
        {% event_preview_description event %}
    """
    return _event_preview_description(event)


@register.simple_tag
def faqpage_structured_data(sections):
    """Generate FAQPage JSON-LD for interview detail pages.

    Usage:
        {% faqpage_structured_data sections %}
    """
    data = build_faqpage_jsonld(sections)
    if not data['mainEntity']:
        return ''
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    return mark_safe(
        f'<script type="application/ld+json">\n{json_str}\n</script>',
    )


@register.simple_tag
def course_learning_path_structured_data(title, description, learning_stages):
    """Generate Course JSON-LD for a learning path page.

    Usage:
        {% course_learning_path_structured_data title description learning_stages %}
    """
    data = build_course_learning_path_jsonld(title, description, learning_stages)
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    return mark_safe(
        f'<script type="application/ld+json">\n{json_str}\n</script>',
    )
