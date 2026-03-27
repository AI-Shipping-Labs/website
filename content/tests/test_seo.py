"""
Tests for SEO features: structured data, meta tags, OpenGraph tags, and sitemap.
"""

import json
from datetime import date

from django.test import TestCase, RequestFactory
from django.template import Template, Context
from django.utils import timezone

from content.models import Article, Course, Module, Unit, Project, Tutorial
from events.models import Event


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
            is_free=True,
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
        self.course.is_free = False
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
            event_type='live',
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
            event_type='live',
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


class MetaTagsInViewTest(TestCase):
    """Test that meta tags appear correctly in rendered pages."""

    def setUp(self):
        self.article = Article.objects.create(
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

    def setUp(self):
        self.course = Course.objects.create(
            title='Python for AI',
            slug='python-for-ai',
            description='Learn Python for AI engineering.',
            status='published',
            is_free=True,
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
    """Test SEO meta tags on recording detail page."""

    def setUp(self):
        self.recording = Event.objects.create(
            title='AI Agents Workshop',
            slug='ai-agents-workshop',
            description='Building AI agents with tools.',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=abc123',
            published=True,
            required_level=0,
        )

    def test_recording_detail_has_canonical_url(self):
        response = self.client.get('/event-recordings/ai-agents-workshop')
        content = response.content.decode()
        self.assertIn(
            '<link rel="canonical" href="https://aishippinglabs.com/event-recordings/ai-agents-workshop">',
            content,
        )

    def test_recording_detail_has_video_jsonld(self):
        response = self.client.get('/event-recordings/ai-agents-workshop')
        content = response.content.decode()
        self.assertIn('"@type": "VideoObject"', content)


class EventDetailSEOTest(TestCase):
    """Test SEO meta tags on event detail page."""

    def setUp(self):
        self.event = Event.objects.create(
            title='Live Workshop',
            slug='live-workshop',
            description='A live coding workshop.',
            event_type='live',
            start_datetime=timezone.now(),
            status='upcoming',
        )

    def test_event_detail_has_canonical_url(self):
        response = self.client.get('/events/live-workshop')
        content = response.content.decode()
        self.assertIn(
            '<link rel="canonical" href="https://aishippinglabs.com/events/live-workshop">',
            content,
        )

    def test_event_detail_has_event_jsonld(self):
        response = self.client.get('/events/live-workshop')
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
        article = Article.objects.create(
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
        import re
        match = re.search(
            r'<meta name="description" content="([^"]*)">', content,
        )
        self.assertIsNotNone(match)
        desc = match.group(1)
        self.assertLessEqual(len(desc), 163)  # Allow for HTML entity encoding


class SitemapTest(TestCase):
    """Test sitemap generation at /sitemap.xml."""

    def setUp(self):
        # Open article (should be in sitemap)
        self.open_article = Article.objects.create(
            title='Open Article',
            slug='open-article',
            description='An open article.',
            content_markdown='# Content',
            date=date(2025, 6, 15),
            published=True,
            required_level=0,
        )
        # Gated article (should NOT be in sitemap)
        self.gated_article = Article.objects.create(
            title='Gated Article',
            slug='gated-article',
            description='A gated article.',
            content_markdown='# Content',
            date=date(2025, 6, 14),
            published=True,
            required_level=1,
        )
        # Draft article (should NOT be in sitemap)
        self.draft_article = Article.objects.create(
            title='Draft Article',
            slug='draft-article',
            description='A draft article.',
            content_markdown='# Content',
            date=date(2025, 6, 13),
            published=False,
        )
        # Published course (always in sitemap)
        self.course = Course.objects.create(
            title='Test Course',
            slug='test-course',
            status='published',
        )
        # Draft course (should NOT be in sitemap)
        self.draft_course = Course.objects.create(
            title='Draft Course',
            slug='draft-course',
            status='draft',
        )
        # Event (non-draft)
        self.event = Event.objects.create(
            title='Test Event',
            slug='test-event',
            event_type='live',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        # Draft event (should NOT be in sitemap)
        self.draft_event = Event.objects.create(
            title='Draft Event',
            slug='draft-event',
            event_type='live',
            start_datetime=timezone.now(),
            status='draft',
        )
        # Open recording
        self.recording = Event.objects.create(
            title='Open Recording',
            slug='open-recording',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=open1',
            published=True,
            required_level=0,
        )
        # Gated recording
        self.gated_recording = Event.objects.create(
            title='Gated Recording',
            slug='gated-recording',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 5, 9, 12, 0)), status='completed',
            recording_url='https://youtube.com/watch?v=gated1',
            published=True,
            required_level=1,
        )
        # Project
        self.project = Project.objects.create(
            title='Open Project',
            slug='open-project',
            date=date(2025, 6, 1),
            published=True,
            required_level=0,
        )
        # Tutorial
        self.tutorial = Tutorial.objects.create(
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
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/events/test-event', content)

    def test_sitemap_excludes_draft_event(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/events/draft-event', content)

    def test_sitemap_includes_open_recording(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertIn('/event-recordings/open-recording', content)

    def test_sitemap_excludes_gated_recording(self):
        response = self.client.get('/sitemap.xml')
        content = response.content.decode()
        self.assertNotIn('/event-recordings/gated-recording', content)

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
        self.assertIn('ai-shipping-labs.jpg', content)

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
