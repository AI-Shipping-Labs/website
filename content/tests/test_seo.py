"""
Tests for SEO features: structured data, meta tags, OpenGraph tags, and sitemap.
"""

import html
import json
import re
from datetime import date, datetime
from datetime import timezone as dt_tz

from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.utils import timezone

from content.access import LEVEL_BASIC
from content.models import (
    Article,
    Course,
    Module,
    Project,
    TagRule,
    Tutorial,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.templatetags.seo_tags import build_seo_description
from events.models import Event


def _meta_content(content, attr, key):
    match = re.search(
        rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">',
        content,
    )
    if match is None:
        return None
    return html.unescape(match.group(1))


def _jsonld_objects(content):
    scripts = re.findall(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        content,
        flags=re.S,
    )
    return [json.loads(script) for script in scripts]


class StructuredDataArticleTest(TestCase):
    """Test JSON-LD structured data generation for articles."""

    def setUp(self):
        self.article = Article.objects.create(
            title='Test Article',
            slug='test-article',
            description='A test article about AI.',
            content_markdown='# Hello World',
            date=date(2025, 6, 15),
            author='Jane Doe',
            cover_image_url='https://example.com/image.jpg',
            published=True,
            required_level=0,
        )

    def test_structured_data_tag_returns_script_tag(self):
        template = Template('{% load seo_tags %}{% structured_data article %}')
        context = Context({'article': self.article})
        result = template.render(context)
        self.assertIn('<script type="application/ld+json">', result)
        self.assertIn('</script>', result)

    def test_structured_data_article_type(self):
        template = Template('{% load seo_tags %}{% structured_data article %}')
        context = Context({'article': self.article})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'Article')
        self.assertEqual(data['@context'], 'https://schema.org')

    def test_structured_data_article_fields(self):
        template = Template('{% load seo_tags %}{% structured_data article %}')
        context = Context({'article': self.article})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['headline'], 'Test Article')
        self.assertEqual(data['description'], 'A test article about AI.')
        self.assertEqual(data['author']['name'], 'Jane Doe')
        self.assertEqual(data['author']['@type'], 'Person')
        self.assertEqual(data['publisher']['name'], 'AI Shipping Labs')
        self.assertEqual(data['image'], 'https://example.com/image.jpg')

    def test_structured_data_article_dates(self):
        template = Template('{% load seo_tags %}{% structured_data article %}')
        context = Context({'article': self.article})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['datePublished'], '2025-06-15')
        self.assertIn('dateModified', data)

    def test_structured_data_article_no_image(self):
        self.article.cover_image_url = ''
        self.article.save()
        template = Template('{% load seo_tags %}{% structured_data article %}')
        context = Context({'article': self.article})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertNotIn('image', data)

    def _extract_jsonld(self, html):
        """Extract JSON-LD data from a script tag."""
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class StructuredDataCourseTest(TestCase):
    """Test JSON-LD structured data generation for courses."""

    def setUp(self):
        self.course = Course.objects.create(
            title='AI Engineering Course',
            slug='ai-engineering',
            description='Learn AI engineering from scratch.',
            status='published',
            cover_image_url='https://example.com/course.jpg',
        )

    def test_structured_data_course_type(self):
        template = Template('{% load seo_tags %}{% structured_data course %}')
        context = Context({'course': self.course})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'Course')

    def test_structured_data_course_fields(self):
        template = Template('{% load seo_tags %}{% structured_data course %}')
        context = Context({'course': self.course})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['name'], 'AI Engineering Course')
        self.assertEqual(data['provider']['name'], 'AI Shipping Labs')
        self.assertEqual(data['image'], 'https://example.com/course.jpg')

    def test_structured_data_free_course_offers(self):
        template = Template('{% load seo_tags %}{% structured_data course %}')
        context = Context({'course': self.course})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertIn('offers', data)
        self.assertEqual(data['offers']['price'], '0')
        self.assertEqual(data['offers']['priceCurrency'], 'EUR')

    def test_structured_data_paid_course_no_offers(self):
        self.course.required_level = LEVEL_BASIC
        self.course.save()
        template = Template('{% load seo_tags %}{% structured_data course %}')
        context = Context({'course': self.course})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertNotIn('offers', data)

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class StructuredDataRecordingTest(TestCase):
    """Test JSON-LD structured data generation for recordings."""

    def setUp(self):
        self.recording = Event.objects.create(
            title='AI Agents Workshop',
            slug='ai-agents-workshop',
            description='Workshop on building AI agents.',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=abc123',
            published=True,
            required_level=0,
        )

    def test_structured_data_recording_with_video(self):
        template = Template('{% load seo_tags %}{% structured_data recording "recording" %}')
        context = Context({'recording': self.recording})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'VideoObject')
        self.assertEqual(data['name'], 'AI Agents Workshop')
        self.assertEqual(data['embedUrl'], 'https://youtube.com/watch?v=abc123')

    def test_structured_data_recording_without_video(self):
        self.recording.recording_url = ''
        self.recording.recording_embed_url = ''
        self.recording.save()
        template = Template('{% load seo_tags %}{% structured_data recording "recording" %}')
        context = Context({'recording': self.recording})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'LearningResource')

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class StructuredDataEventTest(TestCase):
    """Test JSON-LD structured data generation for events."""

    def setUp(self):
        self.event = Event.objects.create(
            title='AI Workshop',
            slug='ai-workshop',
            description='A live AI workshop.',
            start_datetime=timezone.make_aware(
                timezone.datetime(2025, 7, 1, 18, 0),
            ),
            end_datetime=timezone.make_aware(
                timezone.datetime(2025, 7, 1, 20, 0),
            ),
            location='Zoom',
            status='upcoming',
        )

    def test_structured_data_event_type(self):
        template = Template('{% load seo_tags %}{% structured_data event %}')
        context = Context({'event': self.event})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'Event')

    def test_structured_data_event_fields(self):
        template = Template('{% load seo_tags %}{% structured_data event %}')
        context = Context({'event': self.event})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['name'], 'AI Workshop')
        self.assertIn('startDate', data)
        self.assertIn('endDate', data)
        self.assertEqual(data['location']['name'], 'Zoom')
        self.assertEqual(data['organizer']['name'], 'AI Shipping Labs')

    def test_structured_data_event_no_end_date(self):
        self.event.end_datetime = None
        self.event.save()
        template = Template('{% load seo_tags %}{% structured_data event %}')
        context = Context({'event': self.event})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertNotIn('endDate', data)

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class StructuredDataUnitTest(TestCase):
    """Test JSON-LD structured data generation for course units."""

    def setUp(self):
        self.course = Course.objects.create(
            title='Test Course',
            slug='test-course',
            status='published',
        )
        self.module = Module.objects.create(
            course=self.course,
            title='Module 1',
            slug='module-1',
            sort_order=0,
        )
        self.unit = Unit.objects.create(
            module=self.module,
            title='Lesson 1',
            slug='lesson-1',
            sort_order=0,
            video_url='https://youtube.com/watch?v=xyz',
        )

    def test_structured_data_unit_type(self):
        template = Template('{% load seo_tags %}{% structured_data unit %}')
        context = Context({'unit': self.unit})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'LearningResource')

    def test_structured_data_unit_with_video(self):
        template = Template('{% load seo_tags %}{% structured_data unit %}')
        context = Context({'unit': self.unit})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertIn('video', data)
        self.assertEqual(data['video']['@type'], 'VideoObject')

    def test_structured_data_unit_without_video(self):
        self.unit.video_url = ''
        self.unit.save()
        template = Template('{% load seo_tags %}{% structured_data unit %}')
        context = Context({'unit': self.unit})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertNotIn('video', data)

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class StructuredDataOrganizationTest(TestCase):
    """Test JSON-LD structured data for homepage (Organization)."""

    def test_structured_data_no_content_returns_organization(self):
        template = Template('{% load seo_tags %}{% structured_data %}')
        context = Context({})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'Organization')
        self.assertEqual(data['name'], 'AI Shipping Labs')
        self.assertIn('url', data)
        self.assertIn('founder', data)

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class OgTagsTest(TestCase):
    """Test OpenGraph and Twitter Card meta tag generation."""

    def setUp(self):
        self.factory = RequestFactory()
        self.article = Article.objects.create(
            title='Test Article',
            slug='test-article',
            description='A test article about AI engineering.',
            content_markdown='# Hello',
            date=date(2025, 6, 15),
            author='Jane Doe',
            cover_image_url='https://example.com/image.jpg',
            published=True,
        )

    def test_og_tags_includes_og_title(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:title', result)
        self.assertIn('Test Article', result)

    def test_og_tags_includes_og_description(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:description', result)
        self.assertIn('A test article about AI engineering.', result)

    def test_og_tags_includes_og_image(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:image', result)
        self.assertIn('https://example.com/image.jpg', result)

    def test_og_tags_includes_og_url(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:url', result)
        self.assertIn('/blog/test-article', result)

    def test_og_tags_includes_og_type_article(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:type', result)
        self.assertIn('article', result)

    def test_og_tags_includes_og_site_name(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:site_name', result)
        self.assertIn('AI Shipping Labs', result)

    def test_og_tags_includes_twitter_card(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('twitter:card', result)
        self.assertIn('summary_large_image', result)

    def test_og_tags_includes_twitter_title(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('twitter:title', result)

    def test_og_tags_includes_twitter_description(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('twitter:description', result)

    def test_og_tags_includes_twitter_image(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('twitter:image', result)
        self.assertIn('https://example.com/image.jpg', result)

    def test_og_tags_no_image_uses_default_fallback(self):
        self.article.cover_image_url = ''
        self.article.save()
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:image', result)
        self.assertIn('/static/ai-shipping-labs.jpg', result)
        self.assertIn('og:image:alt', result)
        self.assertIn('AI Shipping Labs', result)

    def test_og_tags_homepage_defaults(self):
        template = Template('{% load seo_tags %}{% og_tags %}')
        request = self.factory.get('/')
        context = Context({'request': request})
        result = template.render(context)
        self.assertIn('og:type', result)
        self.assertIn('website', result)
        self.assertIn('AI Shipping Labs', result)

    def test_og_tags_event_type(self):
        event = Event.objects.create(
            title='Workshop',
            slug='workshop',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        template = Template('{% load seo_tags %}{% og_tags event %}')
        request = self.factory.get('/')
        context = Context({'event': event, 'request': request})
        result = template.render(context)
        self.assertIn('event', result)

    def test_og_tags_escapes_special_characters(self):
        self.article.title = 'Test "Article" & <Stuff>'
        self.article.save()
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('&amp;', result)
        self.assertIn('&quot;', result)
        self.assertNotIn('"Article"', result)


class OgImageAutoBannerFallbackTest(TestCase):
    """Issue #895: ``_get_image_url`` falls back to ``auto_banner_url``.

    The operator-supplied ``cover_image_url`` always wins; the
    platform-generated ``auto_banner_url`` is the OG/Twitter image only
    when no cover is set; the site default OG image is the final fallback.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def _render(self, event):
        template = Template('{% load seo_tags %}{% og_tags event %}')
        request = self.factory.get('/')
        context = Context({'event': event, 'request': request})
        return template.render(context)

    def test_auto_banner_used_when_no_cover(self):
        event = Event.objects.create(
            title='Banner Event', slug='banner-event',
            start_datetime=timezone.now(), status='upcoming',
            cover_image_url='',
            auto_banner_url='https://cdn.example.com/banners/event/1.jpg',
        )
        result = self._render(event)
        self.assertIn(
            '<meta property="og:image" content="'
            'https://cdn.example.com/banners/event/1.jpg">',
            result,
        )
        # Twitter image mirrors the same URL with the large-image card.
        self.assertIn(
            '<meta name="twitter:image" content="'
            'https://cdn.example.com/banners/event/1.jpg">',
            result,
        )
        self.assertIn('summary_large_image', result)

    def test_cover_image_wins_over_auto_banner(self):
        event = Event.objects.create(
            title='Cover Event', slug='cover-event',
            start_datetime=timezone.now(), status='upcoming',
            cover_image_url='https://cdn.example.com/manual/cover.png',
            auto_banner_url='https://cdn.example.com/banners/event/2.jpg',
        )
        result = self._render(event)
        self.assertIn(
            '<meta property="og:image" content="'
            'https://cdn.example.com/manual/cover.png">',
            result,
        )
        self.assertNotIn('banners/event/2.jpg', result)

    def test_default_og_image_when_neither_set(self):
        event = Event.objects.create(
            title='Plain Event', slug='plain-event',
            start_datetime=timezone.now(), status='upcoming',
            cover_image_url='', auto_banner_url='',
        )
        result = self._render(event)
        self.assertIn('/static/ai-shipping-labs.jpg', result)
        # No empty og:image value.
        self.assertNotIn('property="og:image" content="">', result)


class WorkshopOgImageAutoBannerFallbackTest(TestCase):
    """Issue #900: cover-less workshops use their generated auto-banner.

    The named regression is workshops, so assert the fallback explicitly
    for the Workshop content type in addition to the shared Event coverage.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def _make_workshop(self, **overrides):
        defaults = {
            'slug': 'vector-search-sqlite',
            'title': 'Vector Search in SQLite',
            'date': date(2026, 4, 13),
            'description': 'A hands-on workshop.',
            'cover_image_url': '',
            'auto_banner_url': '',
        }
        defaults.update(overrides)
        return Workshop.objects.create(**defaults)

    def _render(self, workshop):
        template = Template('{% load seo_tags %}{% og_tags workshop %}')
        request = self.factory.get('/')
        context = Context({'workshop': workshop, 'request': request})
        return template.render(context)

    def test_cover_less_workshop_uses_auto_banner(self):
        workshop = self._make_workshop(
            auto_banner_url='https://cdn.aishippinglabs.com/banners/workshop/9.jpg',
        )
        result = self._render(workshop)
        self.assertIn(
            '<meta property="og:image" content="'
            'https://cdn.aishippinglabs.com/banners/workshop/9.jpg">',
            result,
        )
        self.assertIn(
            '<meta name="twitter:image" content="'
            'https://cdn.aishippinglabs.com/banners/workshop/9.jpg">',
            result,
        )

    def test_cover_less_workshop_without_banner_uses_site_default(self):
        workshop = self._make_workshop()
        result = self._render(workshop)
        self.assertIn('/static/ai-shipping-labs.jpg', result)
        self.assertNotIn('property="og:image" content="">', result)

    def test_display_image_url_prefers_cover_then_banner(self):
        cover = self._make_workshop(
            slug='cover-ws',
            cover_image_url='https://cdn.example.com/manual/cover.png',
            auto_banner_url='https://cdn.example.com/banners/workshop/1.jpg',
        )
        self.assertEqual(
            cover.display_image_url,
            'https://cdn.example.com/manual/cover.png',
        )
        banner_only = self._make_workshop(
            slug='banner-ws',
            auto_banner_url='https://cdn.example.com/banners/workshop/2.jpg',
        )
        self.assertEqual(
            banner_only.display_image_url,
            'https://cdn.example.com/banners/workshop/2.jpg',
        )
        neither = self._make_workshop(slug='plain-ws')
        self.assertEqual(neither.display_image_url, '')


class CustomBannerPrecedenceTest(TestCase):
    """Issue #931: ``custom_banner_url`` sits between cover and auto banner.

    Precedence everywhere a banner/OG image is resolved:
    ``cover_image_url`` -> ``custom_banner_url`` -> ``auto_banner_url``.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def _render(self, obj, var='article'):
        template = Template('{% load seo_tags %}{% og_tags ' + var + ' %}')
        request = self.factory.get('/')
        context = Context({var: obj, 'request': request})
        return template.render(context)

    def test_custom_banner_used_when_no_cover(self):
        article = Article.objects.create(
            title='Custom Banner Article', slug='cb-article',
            date=date(2026, 1, 1),
            cover_image_url='',
            custom_banner_url='https://cdn.example.com/custom-banners/article/7-a.png',
            auto_banner_url='https://cdn.example.com/banners/article/7.jpg',
        )
        result = self._render(article)
        # Custom upload beats the generated banner as the public og:image.
        self.assertIn(
            '<meta property="og:image" content="'
            'https://cdn.example.com/custom-banners/article/7-a.png">',
            result,
        )
        self.assertIn(
            '<meta name="twitter:image" content="'
            'https://cdn.example.com/custom-banners/article/7-a.png">',
            result,
        )
        # The generated banner is NOT used while a custom upload exists.
        self.assertNotIn('banners/article/7.jpg', result)

    def test_frontmatter_cover_wins_over_custom_banner(self):
        article = Article.objects.create(
            title='Cover Wins Article', slug='cover-wins',
            date=date(2026, 1, 1),
            cover_image_url='https://cdn.example.com/manual/cover.png',
            custom_banner_url='https://cdn.example.com/custom-banners/article/8-b.png',
            auto_banner_url='https://cdn.example.com/banners/article/8.jpg',
        )
        result = self._render(article)
        self.assertIn(
            '<meta property="og:image" content="'
            'https://cdn.example.com/manual/cover.png">',
            result,
        )
        # Neither the custom upload nor the generated banner appears.
        self.assertNotIn('custom-banners/article/8-b.png', result)
        self.assertNotIn('banners/article/8.jpg', result)

    def test_workshop_display_image_url_honors_custom_banner(self):
        workshop = Workshop.objects.create(
            slug='cb-ws', title='CB Workshop', date=date(2026, 4, 13),
            cover_image_url='',
            custom_banner_url='https://cdn.example.com/custom-banners/workshop/3-c.png',
            auto_banner_url='https://cdn.example.com/banners/workshop/3.jpg',
        )
        self.assertEqual(
            workshop.display_image_url,
            'https://cdn.example.com/custom-banners/workshop/3-c.png',
        )


class EventPreviewDescriptionTest(TestCase):
    """Issue #817: event link previews lead with the multi-timezone strip."""

    # 2026-05-21T14:00:00Z (a Thursday) renders the canonical strip.
    KNOWN_STRIP = 'Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST'

    def setUp(self):
        self.factory = RequestFactory()

    def _render(self, event):
        template = Template('{% load seo_tags %}{% og_tags event %}')
        request = self.factory.get('/')
        context = Context({'event': event, 'request': request})
        return template.render(context)

    @staticmethod
    def _meta(content, attr, key):
        match = re.search(
            rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">', content,
        )
        return match.group(1) if match else None

    def _make_event(self, **kwargs):
        defaults = {
            'title': 'RAG Live',
            'slug': 'rag-live',
            'start_datetime': datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            'status': 'upcoming',
            'description': 'Build a RAG pipeline live with the community.',
        }
        defaults.update(kwargs)
        return Event.objects.create(**defaults)

    def test_og_description_leads_with_strip_then_description(self):
        event = self._make_event()
        result = self._render(event)
        og_desc = self._meta(result, 'property', 'og:description')
        self.assertTrue(
            og_desc.startswith(f'{self.KNOWN_STRIP} · '),
            f'og:description should start with the tz strip, got: {og_desc!r}',
        )
        self.assertIn('Build a RAG pipeline', og_desc)

    def test_twitter_description_equals_og_description(self):
        event = self._make_event()
        result = self._render(event)
        og_desc = self._meta(result, 'property', 'og:description')
        twitter_desc = self._meta(result, 'name', 'twitter:description')
        self.assertEqual(twitter_desc, og_desc)
        # Sanity: this is the real combined string, not an empty match.
        self.assertIn('NYC', og_desc)

    def test_meta_description_equals_og_description_on_page(self):
        event = self._make_event(slug='meta-match')
        response = self.client.get(event.get_absolute_url())
        content = response.content.decode()
        og_desc = self._meta(content, 'property', 'og:description')
        meta_desc = self._meta(content, 'name', 'description')
        self.assertIsNotNone(og_desc)
        self.assertEqual(meta_desc, og_desc)
        self.assertIn(self.KNOWN_STRIP, meta_desc)

    def test_long_description_truncated_but_strip_survives(self):
        event = self._make_event(description='word ' * 80)  # 400 chars
        result = self._render(event)
        og_desc = self._meta(result, 'property', 'og:description')
        self.assertLessEqual(len(og_desc), 200)
        # The full strip (all four labels) is preserved.
        for label in ('NYC', 'UTC', 'CET', 'IST'):
            self.assertIn(label, og_desc)
        self.assertTrue(og_desc.startswith(f'{self.KNOWN_STRIP} · '))
        self.assertTrue(
            og_desc.endswith('...'),
            f'long description should end with ellipsis, got: {og_desc!r}',
        )

    def test_no_description_renders_strip_only(self):
        event = self._make_event(slug='no-desc', description='')
        result = self._render(event)
        og_desc = self._meta(result, 'property', 'og:description')
        # Exact equality proves strip-only: no trailing separator, no
        # description body, no ellipsis.
        self.assertEqual(og_desc, self.KNOWN_STRIP)
        self.assertFalse(og_desc.endswith(' · '))

    def test_missing_start_datetime_falls_back_to_plain_description(self):
        event = self._make_event(slug='no-time', description='B' * 200)
        # Bypass the NOT NULL constraint on the in-memory instance only.
        event.start_datetime = None
        result = self._render(event)
        og_desc = self._meta(result, 'property', 'og:description')
        for label in ('NYC', 'UTC', 'CET', 'IST'):
            self.assertNotIn(label, og_desc)
        # Old behaviour: truncated to 160 chars (157 + '...').
        self.assertLessEqual(len(og_desc), 160)
        self.assertTrue(og_desc.endswith('...'))

    def test_non_event_content_has_no_time_strip(self):
        article = Article.objects.create(
            title='Plain Article',
            slug='plain-article',
            description='An article about AI engineering.',
            content_markdown='# Hello',
            date=date(2025, 6, 15),
            published=True,
        )
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': article, 'request': request})
        result = template.render(context)
        og_desc = self._meta(result, 'property', 'og:description')
        for label in ('NYC', 'UTC', 'CET', 'IST'):
            self.assertNotIn(label, og_desc)
        self.assertEqual(og_desc, 'An article about AI engineering.')


class SEODescriptionHelperTest(TestCase):
    """Issue #1174: shared SEO descriptions clean markdown and fall back well."""

    def test_prefers_explicit_description_and_cleans_markdown(self):
        article = Article(
            title='Markdown Heavy Article',
            description=(
                'Build **agents** with [retrieval](https://example.com).\n\n'
                '```python\nprint("not a snippet")\n```\n'
                '<strong>Ship faster</strong> ![diagram](diagram.png)'
            ),
        )

        description = build_seo_description(article, 'article')

        self.assertEqual(
            description,
            'Build agents with retrieval. Ship faster',
        )
        self.assertNotIn('**', description)
        self.assertNotIn('```', description)
        self.assertNotIn('print', description)
        self.assertNotIn('![', description)
        self.assertNotIn('<strong>', description)

    def test_truncates_at_word_boundary(self):
        course = Course(
            title='Long Course',
            description=' '.join(f'word{i}' for i in range(40)),
        )

        description = build_seo_description(course, 'course')

        self.assertLessEqual(len(description), 160)
        self.assertTrue(description.endswith('...'))
        self.assertNotRegex(description, r'word\d+$')

    def test_sparse_content_falls_back_to_title_and_type(self):
        project = Project(title='Sparse Project')

        description = build_seo_description(project, 'project')

        self.assertEqual(
            description,
            'Explore Sparse Project, an AI Shipping Labs project.',
        )

    def test_workshop_page_descriptions_include_page_and_parent_context(self):
        workshop = Workshop(
            title='Vector Search Workshop',
            slug='vector-search',
            date=date(2026, 6, 1),
        )
        first_page = WorkshopPage(
            workshop=workshop,
            title='Indexing Documents',
            slug='indexing',
            body='# Indexing Documents\n\nBuild a vector index from markdown files.',
        )
        second_page = WorkshopPage(
            workshop=workshop,
            title='Evaluating Retrieval',
            slug='evaluating',
            body='Measure retrieval quality with labeled examples.',
        )

        first_description = build_seo_description(first_page, 'workshop_page')
        second_description = build_seo_description(second_page, 'workshop_page')

        self.assertIn('Indexing Documents in Vector Search Workshop', first_description)
        self.assertIn('Build a vector index', first_description)
        self.assertIn('Evaluating Retrieval in Vector Search Workshop', second_description)
        self.assertIn('Measure retrieval quality', second_description)
        self.assertNotEqual(first_description, second_description)

    def test_course_unit_description_uses_readable_lesson_prose(self):
        course = Course(title='SEO Course', slug='seo-course')
        module = Module(course=course, title='Module One', slug='module-one')
        unit = Unit(
            module=module,
            title='Lesson One',
            slug='lesson-one',
            body=(
                '# Lesson One\n\n'
                '```python\nprint("setup code")\n```\n'
                'Learn to turn raw notes into concise crawler snippets.\n\n'
                '<em>Readable prose</em> stays.'
            ),
        )

        description = build_seo_description(unit, 'unit')

        self.assertTrue(description.startswith('Lesson One in SEO Course: Learn'))
        self.assertNotIn('#', description)
        self.assertNotIn('```', description)
        self.assertNotIn('setup code', description)
        self.assertNotIn('<em>', description)
        self.assertNotRegex(description, r'\s{2,}')


class SEODescriptionRenderedHeadTest(TestCase):
    """Issue #1174: rendered public pages use cleaned content-specific metadata."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Markdown SEO Article',
            slug='markdown-seo-article',
            description=(
                'A **focused** article with [links](https://example.com) '
                'and <span>HTML</span>.'
            ),
            content_markdown='# Markdown SEO Article\n\nBody text.',
            date=date(2026, 6, 1),
            published=True,
            required_level=0,
        )
        cls.tutorial = Tutorial.objects.create(
            title='Public Tutorial',
            slug='public-tutorial',
            description='Follow a practical tutorial for AI builders.',
            content_markdown='# Public Tutorial\n\nTutorial body.',
            date=date(2026, 6, 2),
            published=True,
            required_level=0,
        )
        cls.project = Project.objects.create(
            title='Sparse Project',
            slug='sparse-project',
            description='',
            content_markdown='',
            date=date(2026, 6, 3),
            published=True,
            required_level=0,
        )
        cls.course = Course.objects.create(
            title='SEO Course',
            slug='seo-course',
            description='A course about crawler-friendly content metadata.',
            status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title='Module One',
            slug='module-one',
            sort_order=1,
            overview=(
                '# Module One\n\n'
                'Learn the overview without repeating the module title twice.'
            ),
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Lesson One',
            slug='lesson-one',
            sort_order=1,
            body=(
                '# Lesson One\n\n'
                '```python\nprint("raw code")\n```\n'
                'Turn lesson notes into readable search snippets.'
            ),
        )
        cls.event = Event.objects.create(
            title='SEO Event',
            slug='seo-event',
            description='A **live** event about AI metadata snippets.',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
            published=True,
            required_level=0,
        )
        cls.workshop = Workshop.objects.create(
            title='SEO Workshop',
            slug='seo-workshop',
            description='A workshop about search previews for AI pages.',
            date=date(2026, 6, 4),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            event=cls.event,
        )
        cls.workshop_page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            title='Clean Tutorial Metadata',
            slug='clean-tutorial-metadata',
            sort_order=1,
            body=(
                '# Clean Tutorial Metadata\n\n'
                'Write page-specific tutorial snippets for crawlers.'
            ),
        )

    def test_article_descriptions_are_cleaned_across_meta_social_and_jsonld(self):
        response = self.client.get(self.article.get_absolute_url())
        content = response.content.decode()

        meta_description = _meta_content(content, 'name', 'description')
        og_description = _meta_content(content, 'property', 'og:description')
        twitter_description = _meta_content(content, 'name', 'twitter:description')
        jsonld = _jsonld_objects(content)[0]

        self.assertEqual(meta_description, 'A focused article with links and HTML.')
        self.assertEqual(og_description, meta_description)
        self.assertEqual(twitter_description, meta_description)
        self.assertEqual(jsonld['description'], meta_description)
        self.assertNotIn('**', meta_description)
        self.assertNotIn('<span>', meta_description)

    def test_workshop_tutorial_page_uses_page_specific_metadata(self):
        response = self.client.get(self.workshop_page.get_absolute_url())
        content = response.content.decode()
        tutorial_url = f'https://aishippinglabs.com{self.workshop_page.get_absolute_url()}'

        meta_description = _meta_content(content, 'name', 'description')
        og_description = _meta_content(content, 'property', 'og:description')
        twitter_description = _meta_content(content, 'name', 'twitter:description')
        og_title = _meta_content(content, 'property', 'og:title')
        og_url = _meta_content(content, 'property', 'og:url')
        jsonld = _jsonld_objects(content)[0]

        self.assertIn('Clean Tutorial Metadata in SEO Workshop', meta_description)
        self.assertIn('page-specific tutorial snippets', meta_description)
        self.assertEqual(og_description, meta_description)
        self.assertEqual(twitter_description, meta_description)
        self.assertEqual(og_title, 'Clean Tutorial Metadata | SEO Workshop')
        self.assertEqual(og_url, tutorial_url)
        self.assertIn(f'<link rel="canonical" href="{tutorial_url}">', content)
        self.assertEqual(jsonld['description'], meta_description)
        self.assertEqual(jsonld['mainEntityOfPage']['@id'], tutorial_url)

    def test_workshop_video_page_uses_recording_metadata_and_url(self):
        response = self.client.get(f'{self.workshop.get_absolute_url()}/video')
        content = response.content.decode()
        video_url = f'https://aishippinglabs.com{self.workshop.get_absolute_url()}/video'

        meta_description = _meta_content(content, 'name', 'description')
        og_title = _meta_content(content, 'property', 'og:title')
        og_url = _meta_content(content, 'property', 'og:url')
        twitter_description = _meta_content(content, 'name', 'twitter:description')
        jsonld = _jsonld_objects(content)[0]

        self.assertTrue(meta_description.startswith('Recording for SEO Workshop:'))
        self.assertEqual(twitter_description, meta_description)
        self.assertEqual(og_title, 'SEO Workshop - Recording')
        self.assertEqual(og_url, video_url)
        self.assertIn(f'<link rel="canonical" href="{video_url}">', content)
        self.assertEqual(jsonld['description'], meta_description)
        self.assertEqual(jsonld['url'], video_url)

    def test_workshop_video_jsonld_omits_protected_s3_recording_url(self):
        s3_event = Event.objects.create(
            title='S3 SEO Event',
            slug='s3-seo-event',
            description='A private S3 recording.',
            start_datetime=timezone.now(),
            status='completed',
            recording_s3_url=(
                'https://private-recordings.s3.amazonaws.com/events/secret.mp4'
                '?X-Amz-Signature=abc123'
            ),
            published=True,
            required_level=0,
        )
        s3_workshop = Workshop.objects.create(
            title='S3 SEO Workshop',
            slug='s3-seo-workshop',
            description='A workshop with private S3 recording media.',
            date=date(2026, 6, 5),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            event=s3_event,
        )

        response = self.client.get(f'{s3_workshop.get_absolute_url()}/video')
        content = response.content.decode()
        jsonld = _jsonld_objects(content)[0]

        self.assertEqual(jsonld['@type'], 'LearningResource')
        self.assertEqual(
            jsonld['url'],
            f'https://aishippinglabs.com{s3_workshop.get_absolute_url()}/video',
        )
        self.assertNotIn('embedUrl', jsonld)
        self.assertNotIn('amazonaws.com', content)
        self.assertNotIn('X-Amz-Signature', content)

    def test_course_unit_and_module_overview_clean_raw_markdown(self):
        unit_response = self.client.get(self.unit.get_absolute_url())
        unit_content = unit_response.content.decode()
        unit_description = _meta_content(unit_content, 'name', 'description')
        unit_jsonld = _jsonld_objects(unit_content)[0]

        self.assertIn('Turn lesson notes into readable search snippets.', unit_description)
        self.assertNotIn('#', unit_description)
        self.assertNotIn('```', unit_description)
        self.assertNotIn('raw code', unit_description)
        self.assertEqual(unit_jsonld['description'], unit_description)

        module_response = self.client.get(self.module.get_absolute_url())
        module_content = module_response.content.decode()
        module_description = _meta_content(module_content, 'name', 'description')

        self.assertEqual(
            module_description.count('Module One'),
            1,
            module_description,
        )
        self.assertIn('Learn the overview', module_description)

    def test_public_detail_surfaces_emit_non_empty_descriptions(self):
        urls = [
            self.workshop.get_absolute_url(),
            self.article.get_absolute_url(),
            self.tutorial.get_absolute_url(),
            self.project.get_absolute_url(),
            self.course.get_absolute_url(),
            self.unit.get_absolute_url(),
            self.module.get_absolute_url(),
            self.event.get_absolute_url(),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (200, 403))
                description = _meta_content(
                    response.content.decode(),
                    'name',
                    'description',
                )
                self.assertIsNotNone(description)
                self.assertNotEqual(description.strip(), '')
                if url != self.event.get_absolute_url():
                    self.assertLessEqual(len(description), 160)

    def test_event_jsonld_preserves_timezone_leading_preview(self):
        response = self.client.get(self.event.get_absolute_url())
        content = response.content.decode()
        meta_description = _meta_content(content, 'name', 'description')
        jsonld = _jsonld_objects(content)[0]

        self.assertTrue(meta_description.startswith(EventPreviewDescriptionTest.KNOWN_STRIP))
        self.assertLessEqual(len(meta_description), 200)
        self.assertEqual(jsonld['description'], meta_description)


class MetaTagsInViewTest(TestCase):
    """Test that meta tags appear correctly in rendered pages."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='SEO Test Article',
            slug='seo-test',
            description='This is a test article for SEO verification.',
            content_markdown='# Content',
            date=date(2025, 6, 15),
            author='Test Author',
            published=True,
            required_level=0,
        )

    def test_blog_detail_has_title_format(self):
        response = self.client.get('/blog/seo-test')
        content = response.content.decode()
        self.assertIn('<title>SEO Test Article | AI Shipping Labs</title>', content)

    def test_blog_detail_has_meta_description(self):
        response = self.client.get('/blog/seo-test')
        content = response.content.decode()
        self.assertIn(
            '<meta name="description" content="This is a test article for SEO verification.">',
            content,
        )

    def test_blog_detail_has_canonical_url(self):
        response = self.client.get('/blog/seo-test')
        content = response.content.decode()
        self.assertIn('<link rel="canonical" href="https://aishippinglabs.com/blog/seo-test">', content)

    def test_blog_detail_has_jsonld(self):
        response = self.client.get('/blog/seo-test')
        content = response.content.decode()
        self.assertIn('<script type="application/ld+json">', content)
        self.assertIn('"@type": "Article"', content)

    def test_blog_detail_has_og_tags(self):
        response = self.client.get('/blog/seo-test')
        content = response.content.decode()
        self.assertIn('og:title', content)
        self.assertIn('og:description', content)
        self.assertIn('og:url', content)
        self.assertIn('og:type', content)
        self.assertIn('og:site_name', content)

    def test_blog_detail_has_twitter_tags(self):
        response = self.client.get('/blog/seo-test')
        content = response.content.decode()
        self.assertIn('twitter:card', content)
        self.assertIn('summary_large_image', content)
        self.assertIn('twitter:title', content)
        self.assertIn('twitter:description', content)


class CourseDetailSEOTest(TestCase):
    """Test SEO meta tags on course detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Python for AI',
            slug='python-for-ai',
            description='Learn Python for AI engineering.',
            status='published',
        )

    def test_course_detail_has_title_format(self):
        response = self.client.get('/courses/python-for-ai')
        content = response.content.decode()
        self.assertIn('<title>Python for AI | AI Shipping Labs</title>', content)

    def test_course_detail_has_canonical_url(self):
        response = self.client.get('/courses/python-for-ai')
        content = response.content.decode()
        self.assertIn(
            '<link rel="canonical" href="https://aishippinglabs.com/courses/python-for-ai">',
            content,
        )

    def test_course_detail_has_course_jsonld(self):
        response = self.client.get('/courses/python-for-ai')
        content = response.content.decode()
        self.assertIn('"@type": "Course"', content)
        self.assertIn('"name": "Python for AI"', content)


class RecordingDetailSEOTest(TestCase):
    """Test SEO meta tags on a completed event page that has a recording.

    Issue #426 made the event detail page announcement-only. The page now
    emits ``Event`` JSON-LD even when ``recording_url`` is populated; the
    canonical recording surface is the linked Workshop's video page, which
    has its own ``VideoObject`` JSON-LD covered in the workshop SEO tests.
    """

    @classmethod
    def setUpTestData(cls):
        cls.recording = Event.objects.create(
            title='AI Agents Workshop',
            slug='ai-agents-workshop',
            description='Building AI agents with tools.',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=abc123',
            published=True,
            required_level=0,
        )

    def test_recording_detail_has_canonical_url(self):
        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        response = self.client.get(self.recording.get_absolute_url())
        content = response.content.decode()
        self.assertIn(
            f'<link rel="canonical" href="https://aishippinglabs.com{self.recording.get_absolute_url()}">',
            content,
        )

    def test_recording_detail_emits_event_jsonld_not_video(self):
        response = self.client.get(self.recording.get_absolute_url())
        content = response.content.decode()
        # The event detail page is announcement-only -> Event schema, not
        # VideoObject. VideoObject lives on the workshop video page.
        self.assertIn('"@type": "Event"', content)
        self.assertNotIn('"@type": "VideoObject"', content)

    def test_recording_detail_event_jsonld_url_points_to_events_path(self):
        response = self.client.get(self.recording.get_absolute_url())
        content = response.content.decode()
        # Issue #673: the Event JSON-LD url is the canonical id+slug URL.
        self.assertIn(
            f'"url": "https://aishippinglabs.com{self.recording.get_absolute_url()}"',
            content,
        )
        self.assertNotIn('/event-recordings/', content)


class EventDetailSEOTest(TestCase):
    """Test SEO meta tags on event detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Live Workshop',
            slug='live-workshop',
            description='A live coding workshop.',
            start_datetime=timezone.now(),
            status='upcoming',
        )

    def test_event_detail_has_canonical_url(self):
        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        response = self.client.get(self.event.get_absolute_url())
        content = response.content.decode()
        self.assertIn(
            f'<link rel="canonical" href="https://aishippinglabs.com{self.event.get_absolute_url()}">',
            content,
        )

    def test_event_detail_has_event_jsonld(self):
        response = self.client.get(self.event.get_absolute_url())
        content = response.content.decode()
        self.assertIn('"@type": "Event"', content)


class HomepageSEOTest(TestCase):
    """Test SEO meta tags on the homepage."""

    def test_homepage_has_organization_jsonld(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('"@type": "Organization"', content)
        self.assertIn('"name": "AI Shipping Labs"', content)

    def test_homepage_has_title(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('<title>', content)
        self.assertIn('AI Shipping Labs', content)

    def test_homepage_has_canonical_url(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('<link rel="canonical" href="https://aishippinglabs.com">', content)

    def test_homepage_has_og_tags(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('og:title', content)
        self.assertIn('og:site_name', content)
        self.assertIn('twitter:card', content)


class DescriptionTruncationTest(TestCase):
    """Test that long descriptions are truncated to 160 chars."""

    def test_long_description_truncated_in_meta(self):
        long_desc = 'A' * 200
        Article.objects.create(
            title='Long Desc',
            slug='long-desc',
            description=long_desc,
            content_markdown='# Content',
            date=date(2025, 6, 15),
            published=True,
            required_level=0,
        )
        response = self.client.get('/blog/long-desc')
        content = response.content.decode()
        # Django truncatechars:160 adds '...' making it exactly 160
        # Find the meta description tag
        match = re.search(
            r'<meta name="description" content="([^"]*)">', content,
        )
        self.assertIsNotNone(match)
        desc = match.group(1)
        self.assertLessEqual(len(desc), 163)  # Allow for HTML entity encoding


class SitemapTest(TestCase):
    """Test sitemap generation at /sitemap.xml."""

    @classmethod
    def setUpTestData(cls):
        # Open article (should be in sitemap)
        cls.open_article = Article.objects.create(
            title='Open Article',
            slug='open-article',
            description='An open article.',
            content_markdown='# Content',
            date=date(2025, 6, 15),
            published=True,
            required_level=0,
        )
        # Gated article (should NOT be in sitemap)
        cls.gated_article = Article.objects.create(
            title='Gated Article',
            slug='gated-article',
            description='A gated article.',
            content_markdown='# Content',
            date=date(2025, 6, 14),
            published=True,
            required_level=1,
        )
        # Draft article (should NOT be in sitemap)
        cls.draft_article = Article.objects.create(
            title='Draft Article',
            slug='draft-article',
            description='A draft article.',
            content_markdown='# Content',
            date=date(2025, 6, 13),
            published=False,
        )
        # Published course (always in sitemap)
        cls.course = Course.objects.create(
            title='Test Course',
            slug='test-course',
            status='published',
        )
        # Draft course (should NOT be in sitemap)
        cls.draft_course = Course.objects.create(
            title='Draft Course',
            slug='draft-course',
            status='draft',
        )
        # Event (non-draft)
        cls.event = Event.objects.create(
            title='Test Event',
            slug='test-event',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        # Draft event (should NOT be in sitemap)
        cls.draft_event = Event.objects.create(
            title='Draft Event',
            slug='draft-event',
            start_datetime=timezone.now(),
            status='draft',
        )
        # Open recording
        cls.recording = Event.objects.create(
            title='Open Recording',
            slug='open-recording',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=open1',
            published=True,
            required_level=0,
        )
        # Gated recording
        cls.gated_recording = Event.objects.create(
            title='Gated Recording',
            slug='gated-recording',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 9, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=gated1',
            published=True,
            required_level=1,
        )
        # Project
        cls.project = Project.objects.create(
            title='Open Project',
            slug='open-project',
            date=date(2025, 6, 1),
            published=True,
            required_level=0,
        )
        # Tutorial
        cls.tutorial = Tutorial.objects.create(
            title='Open Tutorial',
            slug='open-tutorial',
            date=date(2025, 6, 1),
            published=True,
            required_level=0,
        )

    def test_sitemap_returns_200(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)

    def test_sitemap_content_type(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response['Content-Type'], 'application/xml')

    def test_sitemap_includes_open_article(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/blog/open-article', content)

    def test_sitemap_excludes_gated_article(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/blog/gated-article', content)

    def test_sitemap_excludes_draft_article(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/blog/draft-article', content)

    def test_sitemap_includes_published_course(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/courses/test-course', content)

    def test_sitemap_excludes_draft_course(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/courses/draft-course', content)

    def test_sitemap_includes_upcoming_event(self):
        """Issue #673: sitemap URLs use the canonical ``/events/<id>/<slug>``."""
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn(self.event.get_absolute_url(), content)

    def test_sitemap_excludes_draft_event(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn(self.draft_event.get_absolute_url(), content)

    def test_sitemap_includes_open_recording(self):
        # Completed-with-recording events now live on /events/<id>/<slug>
        # (EventSitemap covers all non-draft events including recordings).
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn(self.recording.get_absolute_url(), content)

    def test_sitemap_has_no_event_recordings_urls(self):
        # The legacy /event-recordings/* URLs must no longer appear in the
        # sitemap after consolidating under /events.
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/event-recordings/', content)

    def test_sitemap_includes_project(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/projects/open-project', content)

    def test_sitemap_includes_tutorial(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/tutorials/open-tutorial', content)

    def test_sitemap_includes_static_pages(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        # Django sitemaps use the Sites framework domain (example.com in tests)
        # Check that static pages are present
        self.assertIn('/</loc>', content)  # homepage
        self.assertIn('/about</loc>', content)
        self.assertIn('/blog</loc>', content)
        self.assertIn('/courses</loc>', content)
        self.assertIn('/events</loc>', content)

    def test_sitemap_is_valid_xml(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('<?xml', content)
        self.assertIn('<urlset', content)
        self.assertIn('</urlset>', content)


class SitemapGatedContentExclusionTest(TestCase):
    """Specifically test that gated content is excluded from sitemap."""

    def test_gated_project_excluded(self):
        Project.objects.create(
            title='Gated Project',
            slug='gated-project',
            date=date(2025, 6, 1),
            published=True,
            required_level=2,
        )
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/projects/gated-project', content)

    def test_gated_tutorial_excluded(self):
        Tutorial.objects.create(
            title='Gated Tutorial',
            slug='gated-tutorial',
            date=date(2025, 6, 1),
            published=True,
            required_level=1,
        )
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/tutorials/gated-tutorial', content)

    def test_open_content_included(self):
        Article.objects.create(
            title='Free Article',
            slug='free-article',
            date=date(2025, 6, 1),
            published=True,
            required_level=0,
        )
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/blog/free-article', content)


class SitemapTagPagesTest(TestCase):
    """Test that tag pages are included in the sitemap."""

    def test_sitemap_includes_tags_index(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/tags</loc>', content)

    def test_sitemap_includes_tag_detail_pages(self):
        Article.objects.create(
            title='Tagged Article',
            slug='tagged-article',
            date=date(2025, 6, 1),
            published=True,
            required_level=0,
            tags=['python', 'ai-engineering'],
        )
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/tags/python', content)
        self.assertIn('/tags/ai-engineering', content)

    def test_sitemap_tag_pages_from_multiple_content_types(self):
        Article.objects.create(
            title='Article With Tag',
            slug='article-tag',
            date=date(2025, 6, 1),
            published=True,
            required_level=0,
            tags=['shared-tag'],
        )
        Event.objects.create(
            title='Recording With Tag',
            slug='recording-tag',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            published=True,
            required_level=0,
            tags=['recording-only-tag'],
        )
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/tags/shared-tag', content)
        self.assertIn('/tags/recording-only-tag', content)

    def test_sitemap_no_duplicate_tag_pages(self):
        """Tags from multiple content types should not create duplicates."""
        Article.objects.create(
            title='Article A',
            slug='article-a',
            date=date(2025, 6, 1),
            published=True,
            tags=['duplicate-tag'],
        )
        Event.objects.create(
            title='Recording A',
            slug='recording-a',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            published=True,
            tags=['duplicate-tag'],
        )
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        # Count occurrences of the tag URL
        count = content.count('/tags/duplicate-tag</loc>')
        self.assertEqual(count, 1)


class OgTagsImageDimensionsTest(TestCase):
    """Test og:image:width, og:image:height, og:image:alt in og_tags."""

    def setUp(self):
        self.factory = RequestFactory()
        self.article = Article.objects.create(
            title='Image Test',
            slug='image-test',
            description='Testing image dimensions.',
            content_markdown='# Hello',
            date=date(2025, 6, 15),
            author='Jane Doe',
            cover_image_url='https://example.com/cover.jpg',
            published=True,
        )

    def test_og_image_dimensions_with_content_image(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:image:width', result)
        self.assertIn('1200', result)
        self.assertIn('og:image:height', result)
        self.assertIn('630', result)
        self.assertIn('og:image:alt', result)
        self.assertIn('Image Test', result)

    def test_og_image_dimensions_with_fallback(self):
        self.article.cover_image_url = ''
        self.article.save()
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('og:image:width', result)
        self.assertIn('og:image:height', result)
        self.assertIn('og:image:alt', result)

    def test_twitter_creator_in_og_tags(self):
        template = Template('{% load seo_tags %}{% og_tags article %}')
        request = self.factory.get('/')
        context = Context({'article': self.article, 'request': request})
        result = template.render(context)
        self.assertIn('twitter:creator', result)
        self.assertIn('@Al_Grigor', result)

    def test_twitter_image_always_present(self):
        template = Template('{% load seo_tags %}{% og_tags %}')
        request = self.factory.get('/')
        context = Context({'request': request})
        result = template.render(context)
        self.assertIn('twitter:image', result)
        self.assertIn('/static/ai-shipping-labs.jpg', result)


class OrganizationSameAsTest(TestCase):
    """Test Organization JSON-LD includes sameAs social links."""

    def test_organization_has_same_as(self):
        template = Template('{% load seo_tags %}{% structured_data %}')
        context = Context({})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertIn('sameAs', data)
        self.assertIn('https://twitter.com/Al_Grigor', data['sameAs'])
        self.assertIn('https://github.com/AI-Shipping-Labs', data['sameAs'])

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class FAQPageStructuredDataTest(TestCase):
    """Test FAQPage JSON-LD generation for interview pages."""

    def test_faqpage_with_questions(self):
        sections = [
            {
                'title': 'Basics',
                'qa': [
                    {'question': 'What is AI?', 'answer': 'Artificial Intelligence.'},
                    {'question': 'What is ML?', 'answer': 'Machine Learning.'},
                ],
            },
            {
                'title': 'Advanced',
                'qa': [
                    {'question': 'What is deep learning?', 'answer': 'A subset of ML.'},
                ],
            },
        ]
        template = Template('{% load seo_tags %}{% faqpage_structured_data sections %}')
        context = Context({'sections': sections})
        result = template.render(context)
        self.assertIn('application/ld+json', result)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'FAQPage')
        self.assertEqual(len(data['mainEntity']), 3)
        self.assertEqual(data['mainEntity'][0]['@type'], 'Question')
        self.assertEqual(data['mainEntity'][0]['name'], 'What is AI?')
        self.assertEqual(
            data['mainEntity'][0]['acceptedAnswer']['text'],
            'Artificial Intelligence.',
        )

    def test_faqpage_empty_sections(self):
        sections = []
        template = Template('{% load seo_tags %}{% faqpage_structured_data sections %}')
        context = Context({'sections': sections})
        result = template.render(context)
        # Should return empty string when no questions
        self.assertNotIn('application/ld+json', result)

    def test_faqpage_question_without_answer_uses_question_text(self):
        sections = [
            {
                'title': 'Test',
                'qa': [
                    {'question': 'What is AI?'},
                ],
            },
        ]
        template = Template('{% load seo_tags %}{% faqpage_structured_data sections %}')
        context = Context({'sections': sections})
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(
            data['mainEntity'][0]['acceptedAnswer']['text'],
            'What is AI?',
        )

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class CourseLearningPathStructuredDataTest(TestCase):
    """Test Course JSON-LD generation for learning path pages."""

    def test_course_learning_path_basic(self):
        template = Template(
            '{% load seo_tags %}'
            '{% course_learning_path_structured_data title description stages %}',
        )
        context = Context({
            'title': 'AI Engineer Learning Path',
            'description': 'A roadmap to become an AI engineer.',
            'stages': [
                {'title': 'Stage 1: Foundations', 'items': ['Python', 'Math']},
                {'title': 'Stage 2: ML Basics', 'items': ['Supervised', 'Unsupervised']},
            ],
        })
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'Course')
        self.assertEqual(data['name'], 'AI Engineer Learning Path')
        self.assertIn('hasPart', data)
        self.assertEqual(len(data['hasPart']), 2)
        self.assertEqual(data['hasPart'][0]['@type'], 'CourseInstance')
        self.assertEqual(data['hasPart'][0]['name'], 'Stage 1: Foundations')
        self.assertIn('Python', data['hasPart'][0]['description'])

    def test_course_learning_path_no_stages(self):
        template = Template(
            '{% load seo_tags %}'
            '{% course_learning_path_structured_data title description stages %}',
        )
        context = Context({
            'title': 'AI Engineer',
            'description': 'A roadmap.',
            'stages': [],
        })
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['@type'], 'Course')
        self.assertNotIn('hasPart', data)

    def test_course_learning_path_has_provider(self):
        template = Template(
            '{% load seo_tags %}'
            '{% course_learning_path_structured_data title description stages %}',
        )
        context = Context({
            'title': 'Path',
            'description': 'Desc',
            'stages': [],
        })
        result = template.render(context)
        data = self._extract_jsonld(result)
        self.assertEqual(data['provider']['name'], 'AI Shipping Labs')

    def _extract_jsonld(self, html):
        start = html.index('<script type="application/ld+json">') + len(
            '<script type="application/ld+json">',
        )
        end = html.index('</script>')
        return json.loads(html[start:end])


class BaseHtmlMetaTagsTest(TestCase):
    """Test meta tags rendered in base.html via homepage."""

    def test_homepage_has_robots_meta(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn(
            'max-snippet:-1, max-image-preview:large, max-video-preview:-1',
            content,
        )

    def test_homepage_has_author_meta(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('<meta name="author" content="Alexey Grigorev">', content)

    def test_homepage_has_twitter_creator(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('twitter:creator', content)
        self.assertIn('@Al_Grigor', content)

    def test_homepage_has_favicon(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('rel="icon"', content)
        self.assertIn('rocket-upscale-no-bg-small.png', content)

    def test_homepage_has_apple_touch_icon(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('rel="apple-touch-icon"', content)

    def test_homepage_has_default_og_image(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('og:image', content)
        # base.html renders {% static 'ai-shipping-labs.jpg' %}, which
        # CompressedManifestStaticFilesStorage rewrites to a hashed
        # filename (ai-shipping-labs.<hash>.jpg) once collectstatic has
        # run in CI. Match either form. See #333.
        self.assertRegex(content, r'ai-shipping-labs(\.[0-9a-f]+)?\.jpg')

    def test_homepage_has_og_image_dimensions(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('og:image:width', content)
        self.assertIn('og:image:height', content)

    def test_homepage_has_verification_block(self):
        """The verification block should exist in base.html (empty by default)."""
        response = self.client.get('/')
        # The block exists but is empty; just verify the page renders fine
        self.assertEqual(response.status_code, 200)

    def test_homepage_organization_has_same_as(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('sameAs', content)
        self.assertIn('https://twitter.com/Al_Grigor', content)
        self.assertIn('https://github.com/AI-Shipping-Labs', content)


# --- Conversions from playwright_tests/test_seo_tags.py (issue #256) ---


class TagRuleInjectionTest(TestCase):
    """Behaviour previously covered by Playwright Scenarios 7 and 8 on
    article detail pages. Tag rules render as plain HTML server-side, so
    the injection (and absence) is verified entirely with assertContains.
    """

    def test_course_promo_injected_after_article_content(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario7TagRuleInjection::test_course_promo_injected_after_article_content
        Article.objects.create(
            title='Getting Started with AI Engineering',
            slug='getting-started-with-ai-engineering',
            description='A guide to AI engineering.',
            content_markdown=(
                '# Getting Started with AI Engineering\n\n'
                'This is the article body about AI engineering.'
            ),
            date=date(2026, 2, 1),
            tags=['ai-engineering'], published=True,
        )
        Course.objects.create(
            title='Python Data AI', slug='python-data-ai',
            tags=['ai-engineering'], status='published',
        )
        TagRule.objects.create(
            tag='ai-engineering',
            component_type='course_promo',
            component_config={
                'title': 'Recommended Course',
                'course_slug': 'python-data-ai',
                'cta_text': 'Start learning',
            },
            position='after_content',
        )

        response = self.client.get(
            '/blog/getting-started-with-ai-engineering',
        )
        self.assertEqual(response.status_code, 200)

        # Article content rendered as usual.
        self.assertContains(response, 'Getting Started with AI Engineering')
        self.assertContains(response, 'article body about AI engineering')

        # The course promo component is injected with title, CTA text,
        # and a link to the configured course.
        self.assertContains(response, 'tag-rule-component')
        self.assertContains(response, 'tag-rule-course_promo')
        self.assertContains(response, 'Recommended Course')
        self.assertContains(response, 'Start learning')
        self.assertContains(response, 'href="/courses/python-data-ai"')

        # The view context exposes the rule at the after_content slot.
        rules = response.context['tag_rules']['after_content']
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].tag, 'ai-engineering')

    def test_no_injected_components_for_unmatched_tags(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario8NoMatchingTagRules::test_no_injected_components_for_unmatched_tags
        Article.objects.create(
            title='Intro to Go', slug='intro-to-go',
            description='A Go language introduction.',
            content_markdown=(
                '# Intro to Go\n\nThis is the article body about Go.'
            ),
            date=date(2026, 2, 1),
            tags=['golang'], published=True,
        )
        TagRule.objects.create(
            tag='ai-engineering',
            component_type='course_promo',
            component_config={
                'title': 'Recommended Course',
                'course_slug': 'python-data-ai',
                'cta_text': 'Start learning',
            },
            position='after_content',
        )

        response = self.client.get('/blog/intro-to-go')
        self.assertEqual(response.status_code, 200)

        # Article content still renders.
        self.assertContains(response, 'Intro to Go')
        self.assertContains(response, 'article body about Go')

        # No tag-rule component appears, and the CTA configured for
        # the unrelated rule is absent.
        self.assertNotContains(response, 'tag-rule-component')
        self.assertNotContains(response, 'Recommended Course')
        self.assertNotContains(response, 'Start learning')

        # Context confirms no rules attach to the after_content slot.
        self.assertEqual(
            len(response.context['tag_rules']['after_content']), 0,
        )
