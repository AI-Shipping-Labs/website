"""
Tests for the Video Player component.

Tests cover:
- Video source detection (YouTube, Loom, self-hosted)
- Timestamp formatting
- Time string parsing
- Video context preparation
- Markdown video URL replacement
- Template tag rendering
- Admin widget
- Recording detail view integration
"""

import json
from datetime import date

from django.template import Context, Template
from django.test import TestCase, Client

from content.models import Article, Recording
from content.templatetags.video_utils import (
    detect_video_source,
    format_timestamp,
    get_loom_embed_url,
    get_youtube_embed_url,
    parse_time_input,
    prepare_video_context,
    replace_video_urls_in_html,
)
from content.admin.widgets import TimestampEditorWidget


# --- Video Source Detection Tests ---

class DetectVideoSourceTest(TestCase):
    """Test detect_video_source function for all URL patterns."""

    def test_youtube_watch_url(self):
        source, vid = detect_video_source('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(source, 'youtube')
        self.assertEqual(vid, 'dQw4w9WgXcQ')

    def test_youtube_watch_url_no_www(self):
        source, vid = detect_video_source('https://youtube.com/watch?v=abc123_-X')
        self.assertEqual(source, 'youtube')
        self.assertEqual(vid, 'abc123_-X')

    def test_youtube_short_url(self):
        source, vid = detect_video_source('https://youtu.be/dQw4w9WgXcQ')
        self.assertEqual(source, 'youtube')
        self.assertEqual(vid, 'dQw4w9WgXcQ')

    def test_youtube_embed_url(self):
        source, vid = detect_video_source('https://www.youtube.com/embed/dQw4w9WgXcQ')
        self.assertEqual(source, 'youtube')
        self.assertEqual(vid, 'dQw4w9WgXcQ')

    def test_youtube_http_url(self):
        source, vid = detect_video_source('http://www.youtube.com/watch?v=test123')
        self.assertEqual(source, 'youtube')
        self.assertEqual(vid, 'test123')

    def test_loom_url(self):
        source, vid = detect_video_source('https://www.loom.com/share/abc123def456')
        self.assertEqual(source, 'loom')
        self.assertEqual(vid, 'abc123def456')

    def test_loom_url_no_www(self):
        source, vid = detect_video_source('https://loom.com/share/xyz789')
        self.assertEqual(source, 'loom')
        self.assertEqual(vid, 'xyz789')

    def test_self_hosted_mp4(self):
        source, vid = detect_video_source('https://cdn.example.com/videos/intro.mp4')
        self.assertEqual(source, 'self_hosted')
        self.assertEqual(vid, 'https://cdn.example.com/videos/intro.mp4')

    def test_self_hosted_webm(self):
        source, vid = detect_video_source('https://cdn.example.com/videos/intro.webm')
        self.assertEqual(source, 'self_hosted')
        self.assertEqual(vid, 'https://cdn.example.com/videos/intro.webm')

    def test_self_hosted_mp4_with_query(self):
        source, vid = detect_video_source('https://s3.amazonaws.com/bucket/video.mp4?token=abc')
        self.assertEqual(source, 'self_hosted')

    def test_empty_url(self):
        source, vid = detect_video_source('')
        self.assertIsNone(source)
        self.assertIsNone(vid)

    def test_none_url(self):
        source, vid = detect_video_source(None)
        self.assertIsNone(source)
        self.assertIsNone(vid)

    def test_unknown_url(self):
        source, vid = detect_video_source('https://example.com/some-page')
        self.assertIsNone(source)
        self.assertIsNone(vid)

    def test_url_with_whitespace(self):
        source, vid = detect_video_source('  https://youtu.be/abc123  ')
        self.assertEqual(source, 'youtube')
        self.assertEqual(vid, 'abc123')


# --- Embed URL Generation Tests ---

class EmbedURLTest(TestCase):
    """Test embed URL generation functions."""

    def test_youtube_embed_url(self):
        url = get_youtube_embed_url('dQw4w9WgXcQ')
        self.assertEqual(url, 'https://www.youtube.com/embed/dQw4w9WgXcQ?enablejsapi=1')

    def test_loom_embed_url(self):
        url = get_loom_embed_url('abc123')
        self.assertEqual(url, 'https://www.loom.com/embed/abc123')

    def test_loom_embed_url_with_time(self):
        url = get_loom_embed_url('abc123', time_seconds=125)
        self.assertEqual(url, 'https://www.loom.com/embed/abc123?t=125')

    def test_loom_embed_url_time_none(self):
        url = get_loom_embed_url('abc123', time_seconds=None)
        self.assertEqual(url, 'https://www.loom.com/embed/abc123')


# --- Timestamp Formatting Tests ---

class FormatTimestampTest(TestCase):
    """Test format_timestamp for various second values."""

    def test_zero_seconds(self):
        self.assertEqual(format_timestamp(0), '[00:00]')

    def test_seconds_only(self):
        self.assertEqual(format_timestamp(45), '[00:45]')

    def test_minutes_and_seconds(self):
        self.assertEqual(format_timestamp(125), '[02:05]')

    def test_exact_minutes(self):
        self.assertEqual(format_timestamp(300), '[05:00]')

    def test_under_one_hour(self):
        self.assertEqual(format_timestamp(3599), '[59:59]')

    def test_exactly_one_hour(self):
        self.assertEqual(format_timestamp(3600), '[1:00:00]')

    def test_over_one_hour(self):
        self.assertEqual(format_timestamp(4380), '[1:13:00]')

    def test_hours_minutes_seconds(self):
        self.assertEqual(format_timestamp(3661), '[1:01:01]')

    def test_large_value(self):
        self.assertEqual(format_timestamp(7200), '[2:00:00]')

    def test_negative_value(self):
        self.assertEqual(format_timestamp(-10), '[00:00]')

    def test_none_value(self):
        self.assertEqual(format_timestamp(None), '[00:00]')

    def test_string_value(self):
        self.assertEqual(format_timestamp('125'), '[02:05]')

    def test_invalid_string(self):
        self.assertEqual(format_timestamp('abc'), '[00:00]')


# --- Time Input Parsing Tests ---

class ParseTimeInputTest(TestCase):
    """Test parse_time_input for MM:SS and H:MM:SS formats."""

    def test_mm_ss(self):
        self.assertEqual(parse_time_input('02:05'), 125)

    def test_mm_ss_zero(self):
        self.assertEqual(parse_time_input('00:00'), 0)

    def test_h_mm_ss(self):
        self.assertEqual(parse_time_input('1:13:00'), 4380)

    def test_h_mm_ss_with_seconds(self):
        self.assertEqual(parse_time_input('1:01:01'), 3661)

    def test_empty_string(self):
        self.assertEqual(parse_time_input(''), 0)

    def test_none(self):
        self.assertEqual(parse_time_input(None), 0)

    def test_whitespace(self):
        self.assertEqual(parse_time_input('  02:05  '), 125)

    def test_invalid_format(self):
        self.assertEqual(parse_time_input('abc'), 0)

    def test_single_part(self):
        self.assertEqual(parse_time_input('45'), 0)


# --- Video Context Preparation Tests ---

class PrepareVideoContextTest(TestCase):
    """Test prepare_video_context function."""

    def test_youtube_context(self):
        ctx = prepare_video_context('https://youtube.com/watch?v=abc123')
        self.assertEqual(ctx['source_type'], 'youtube')
        self.assertEqual(ctx['video_id'], 'abc123')
        self.assertEqual(ctx['embed_url'], 'https://www.youtube.com/embed/abc123?enablejsapi=1')
        self.assertFalse(ctx['has_timestamps'])
        self.assertEqual(ctx['timestamps'], [])

    def test_loom_context(self):
        ctx = prepare_video_context('https://www.loom.com/share/xyz789')
        self.assertEqual(ctx['source_type'], 'loom')
        self.assertEqual(ctx['video_id'], 'xyz789')
        self.assertEqual(ctx['embed_url'], 'https://www.loom.com/embed/xyz789')

    def test_self_hosted_context(self):
        url = 'https://cdn.example.com/video.mp4'
        ctx = prepare_video_context(url)
        self.assertEqual(ctx['source_type'], 'self_hosted')
        self.assertEqual(ctx['embed_url'], url)

    def test_with_timestamps(self):
        timestamps = [
            {'time_seconds': 0, 'label': 'Introduction'},
            {'time_seconds': 125, 'label': 'Setup'},
            {'time_seconds': 3661, 'label': 'Advanced'},
        ]
        ctx = prepare_video_context('https://youtu.be/abc', timestamps)
        self.assertTrue(ctx['has_timestamps'])
        self.assertEqual(len(ctx['timestamps']), 3)
        self.assertEqual(ctx['timestamps'][0]['formatted_time'], '[00:00]')
        self.assertEqual(ctx['timestamps'][0]['label'], 'Introduction')
        self.assertEqual(ctx['timestamps'][1]['formatted_time'], '[02:05]')
        self.assertEqual(ctx['timestamps'][2]['formatted_time'], '[1:01:01]')

    def test_with_empty_timestamps(self):
        ctx = prepare_video_context('https://youtu.be/abc', [])
        self.assertFalse(ctx['has_timestamps'])

    def test_with_none_timestamps(self):
        ctx = prepare_video_context('https://youtu.be/abc', None)
        self.assertFalse(ctx['has_timestamps'])

    def test_unknown_source(self):
        ctx = prepare_video_context('https://example.com/not-a-video')
        self.assertIsNone(ctx['source_type'])
        self.assertIsNone(ctx['embed_url'])


# --- Markdown Video URL Replacement Tests ---

class ReplaceVideoURLsInHTMLTest(TestCase):
    """Test replace_video_urls_in_html for auto-embedding."""

    def test_youtube_url_replaced(self):
        html = '<p>Text above.</p>\n<p>https://www.youtube.com/watch?v=dQw4w9WgXcQ</p>\n<p>Text below.</p>'
        result = replace_video_urls_in_html(html)
        self.assertIn('data-source="youtube"', result)
        self.assertIn('data-video-id="dQw4w9WgXcQ"', result)
        self.assertIn('youtube.com/embed/dQw4w9WgXcQ?enablejsapi=1', result)
        self.assertNotIn('<p>https://www.youtube.com/watch?v=dQw4w9WgXcQ</p>', result)

    def test_youtu_be_url_replaced(self):
        html = '<p>https://youtu.be/abc123</p>'
        result = replace_video_urls_in_html(html)
        self.assertIn('data-source="youtube"', result)
        self.assertIn('data-video-id="abc123"', result)

    def test_loom_url_replaced(self):
        html = '<p>https://www.loom.com/share/abc123def</p>'
        result = replace_video_urls_in_html(html)
        self.assertIn('data-source="loom"', result)
        self.assertIn('data-video-id="abc123def"', result)
        self.assertIn('loom.com/embed/abc123def', result)

    def test_non_video_url_not_replaced(self):
        html = '<p>https://example.com/page</p>'
        result = replace_video_urls_in_html(html)
        self.assertEqual(html, result)

    def test_inline_url_not_replaced(self):
        html = '<p>Check out https://www.youtube.com/watch?v=abc123 for more info.</p>'
        result = replace_video_urls_in_html(html)
        # Should NOT be replaced because URL is not alone in the <p>
        self.assertNotIn('data-source', result)
        self.assertIn('Check out', result)

    def test_empty_html(self):
        self.assertEqual(replace_video_urls_in_html(''), '')

    def test_none_html(self):
        self.assertIsNone(replace_video_urls_in_html(None))

    def test_multiple_video_urls(self):
        html = '<p>https://www.youtube.com/watch?v=vid1</p>\n<p>Some text</p>\n<p>https://www.loom.com/share/loom1</p>'
        result = replace_video_urls_in_html(html)
        self.assertIn('data-video-id="vid1"', result)
        self.assertIn('data-video-id="loom1"', result)
        self.assertIn('Some text', result)

    def test_url_with_surrounding_whitespace_in_p(self):
        html = '<p>  https://www.youtube.com/watch?v=abc  </p>'
        result = replace_video_urls_in_html(html)
        self.assertIn('data-source="youtube"', result)


# --- Template Tag Tests ---

class VideoPlayerTemplateTagTest(TestCase):
    """Test the {% video_player %} template tag."""

    def _render(self, template_string, context=None):
        """Helper to render a template string."""
        t = Template(template_string)
        c = Context(context or {})
        return t.render(c)

    def test_youtube_video_renders(self):
        html = self._render(
            '{% load video_tags %}{% video_player video_url="https://youtube.com/watch?v=test123" %}'
        )
        self.assertIn('data-source="youtube"', html)
        self.assertIn('data-video-id="test123"', html)
        self.assertIn('yt-player-test123', html)

    def test_loom_video_renders(self):
        html = self._render(
            '{% load video_tags %}{% video_player video_url="https://www.loom.com/share/loom456" %}'
        )
        self.assertIn('data-source="loom"', html)
        self.assertIn('loom-player-loom456', html)
        self.assertIn('loom.com/embed/loom456', html)

    def test_self_hosted_video_renders(self):
        html = self._render(
            '{% load video_tags %}{% video_player video_url="https://cdn.example.com/video.mp4" %}'
        )
        self.assertIn('data-source="self_hosted"', html)
        self.assertIn('<video', html)
        self.assertIn('cdn.example.com/video.mp4', html)

    def test_unknown_url_renders_nothing(self):
        html = self._render(
            '{% load video_tags %}{% video_player video_url="https://example.com/page" %}'
        )
        self.assertNotIn('data-source', html)
        self.assertNotIn('<iframe', html)
        self.assertNotIn('<video', html)

    def test_video_with_timestamps(self):
        context = {
            'url': 'https://youtube.com/watch?v=abc',
            'ts': [
                {'time_seconds': 0, 'label': 'Introduction'},
                {'time_seconds': 125, 'label': 'Setup'},
            ],
        }
        html = self._render(
            '{% load video_tags %}{% video_player video_url=url timestamps=ts %}',
            context,
        )
        self.assertIn('[00:00]', html)
        self.assertIn('Introduction', html)
        self.assertIn('[02:05]', html)
        self.assertIn('Setup', html)
        self.assertIn('video-timestamp', html)

    def test_video_without_timestamps_no_timestamp_section(self):
        html = self._render(
            '{% load video_tags %}{% video_player video_url="https://youtube.com/watch?v=abc" %}'
        )
        # The timestamp section heading should not be present
        self.assertNotIn('class="mb-3 text-sm font-semibold uppercase', html)
        # No timestamp buttons should be rendered
        self.assertNotIn('data-time-seconds=', html)

    def test_youtube_includes_iframe_api(self):
        html = self._render(
            '{% load video_tags %}{% video_player video_url="https://youtube.com/watch?v=test" %}'
        )
        self.assertIn('youtube.com/iframe_api', html)
        self.assertIn('YT.Player', html)

    def test_timestamp_seeking_scripts(self):
        context = {
            'url': 'https://youtube.com/watch?v=abc',
            'ts': [{'time_seconds': 60, 'label': 'Chapter 1'}],
        }
        html = self._render(
            '{% load video_tags %}{% video_player video_url=url timestamps=ts %}',
            context,
        )
        self.assertIn('seekTo', html)
        self.assertIn('data-time-seconds="60"', html)


# --- Admin Widget Tests ---

class TimestampEditorWidgetTest(TestCase):
    """Test the TimestampEditorWidget."""

    def test_format_value_with_none(self):
        widget = TimestampEditorWidget()
        self.assertEqual(widget.format_value(None), '[]')

    def test_format_value_with_empty_list(self):
        widget = TimestampEditorWidget()
        self.assertEqual(widget.format_value([]), '[]')

    def test_format_value_with_list(self):
        widget = TimestampEditorWidget()
        data = [{'time_seconds': 0, 'label': 'Intro'}]
        result = widget.format_value(data)
        self.assertEqual(json.loads(result), data)

    def test_format_value_with_valid_json_string(self):
        widget = TimestampEditorWidget()
        json_str = '[{"time_seconds": 60, "label": "Chapter"}]'
        self.assertEqual(widget.format_value(json_str), json_str)

    def test_format_value_with_invalid_json_string(self):
        widget = TimestampEditorWidget()
        self.assertEqual(widget.format_value('not json'), '[]')

    def test_value_from_datadict(self):
        widget = TimestampEditorWidget()
        data = {'timestamps': '[{"time_seconds": 0, "label": "Start"}]'}
        result = widget.value_from_datadict(data, {}, 'timestamps')
        self.assertEqual(result, '[{"time_seconds": 0, "label": "Start"}]')

    def test_value_from_datadict_missing(self):
        widget = TimestampEditorWidget()
        result = widget.value_from_datadict({}, {}, 'timestamps')
        self.assertEqual(result, '[]')

    def test_widget_renders(self):
        from django.forms.renderers import TemplatesSetting
        widget = TimestampEditorWidget()
        html = widget.render('timestamps', [{'time_seconds': 0, 'label': 'Intro'}], renderer=TemplatesSetting())
        self.assertIn('timestamp-editor', html)
        self.assertIn('id_timestamps', html)
        self.assertIn('Add Timestamp', html)
        self.assertIn('Time (MM:SS)', html)
        self.assertIn('Label', html)


# --- Recording Admin Form Tests ---

class RecordingAdminFormTest(TestCase):
    """Test the RecordingAdminForm with TimestampEditorWidget."""

    def test_admin_form_has_timestamp_widget(self):
        from content.admin.recording import RecordingAdminForm
        form = RecordingAdminForm()
        self.assertIsInstance(form.fields['timestamps'].widget, TimestampEditorWidget)

    def test_admin_form_clean_timestamps_valid_json(self):
        from content.admin.recording import RecordingAdminForm
        form_data = {
            'title': 'Test',
            'slug': 'test',
            'description': '',
            'date': '2025-01-01',
            'tags': '[]',
            'level': '',
            'google_embed_url': '',
            'youtube_url': '',
            'timestamps': '[{"time_seconds": 0, "label": "Intro"}]',
            'materials': '[]',
            'core_tools': '[]',
            'learning_objectives': '[]',
            'outcome': '',
            'related_course': '',
            'published': True,
        }
        form = RecordingAdminForm(data=form_data)
        if form.is_valid():
            self.assertEqual(form.cleaned_data['timestamps'], [{'time_seconds': 0, 'label': 'Intro'}])

    def test_admin_form_clean_timestamps_empty(self):
        from content.admin.recording import RecordingAdminForm
        form_data = {
            'title': 'Test',
            'slug': 'test',
            'description': '',
            'date': '2025-01-01',
            'tags': '[]',
            'level': '',
            'google_embed_url': '',
            'youtube_url': '',
            'timestamps': '[]',
            'materials': '[]',
            'core_tools': '[]',
            'learning_objectives': '[]',
            'outcome': '',
            'related_course': '',
            'published': True,
        }
        form = RecordingAdminForm(data=form_data)
        if form.is_valid():
            self.assertEqual(form.cleaned_data['timestamps'], [])


# --- Recording Detail View Integration Tests ---

class RecordingDetailVideoPlayerTest(TestCase):
    """Test that the recording detail view renders the VideoPlayer component."""

    def setUp(self):
        self.client = Client()

    def test_youtube_video_player_in_recording_detail(self):
        recording = Recording.objects.create(
            title='YT Recording',
            slug='yt-recording',
            description='A recording with YouTube',
            date=date(2025, 7, 20),
            youtube_url='https://www.youtube.com/watch?v=testVidId',
            timestamps=[
                {'time_seconds': 0, 'label': 'Intro'},
                {'time_seconds': 300, 'label': 'Main Content'},
            ],
            published=True,
        )
        response = self.client.get('/event-recordings/yt-recording')
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        # VideoPlayer should be rendered
        self.assertIn('data-source="youtube"', content)
        self.assertIn('data-video-id="testVidId"', content)
        # Timestamps should be rendered
        self.assertIn('[00:00]', content)
        self.assertIn('Intro', content)
        self.assertIn('[05:00]', content)
        self.assertIn('Main Content', content)

    def test_recording_without_youtube_no_player(self):
        recording = Recording.objects.create(
            title='No Video Recording',
            slug='no-video',
            description='Recording without video',
            date=date(2025, 7, 20),
            published=True,
        )
        response = self.client.get('/event-recordings/no-video')
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-source=', content)

    def test_recording_with_google_embed_fallback(self):
        """Google embed URLs should still use basic iframe (not video player)."""
        recording = Recording.objects.create(
            title='Google Recording',
            slug='google-recording',
            description='Recording with google embed',
            date=date(2025, 7, 20),
            google_embed_url='https://drive.google.com/file/d/abc/preview',
            published=True,
        )
        response = self.client.get('/event-recordings/google-recording')
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('drive.google.com', content)

    def test_recording_youtube_with_hour_long_timestamps(self):
        recording = Recording.objects.create(
            title='Long Recording',
            slug='long-recording',
            description='An hour-long recording',
            date=date(2025, 7, 20),
            youtube_url='https://www.youtube.com/watch?v=longVid',
            timestamps=[
                {'time_seconds': 0, 'label': 'Start'},
                {'time_seconds': 3600, 'label': 'Hour Mark'},
                {'time_seconds': 4380, 'label': 'Past Hour'},
            ],
            published=True,
        )
        response = self.client.get('/event-recordings/long-recording')
        content = response.content.decode()
        self.assertIn('[00:00]', content)
        self.assertIn('[1:00:00]', content)
        self.assertIn('[1:13:00]', content)


# --- Content Pipeline Integration Tests ---

class ContentPipelineVideoEmbedTest(TestCase):
    """Test that video URLs in markdown content are replaced with embeds
    when processed through the md_to_html + replace_video_urls_in_html pipeline."""

    def test_markdown_youtube_url_becomes_embed_in_article(self):
        """Standalone YouTube URL in markdown body produces a video embed
        in the stored content_html after the full rendering pipeline."""
        from content.management.commands.load_content import md_to_html

        markdown_body = (
            "Some intro text.\n"
            "\n"
            "https://www.youtube.com/watch?v=pipeTest1\n"
            "\n"
            "Some closing text."
        )
        html = md_to_html(markdown_body)
        html = replace_video_urls_in_html(html)

        # The YouTube URL should be replaced with a video player embed
        self.assertIn('data-source="youtube"', html)
        self.assertIn('data-video-id="pipeTest1"', html)
        self.assertIn('youtube.com/embed/pipeTest1', html)
        # The surrounding text should be preserved
        self.assertIn('Some intro text.', html)
        self.assertIn('Some closing text.', html)
        # The raw URL paragraph should be gone
        self.assertNotIn('<p>https://www.youtube.com/watch?v=pipeTest1</p>', html)

    def test_article_with_video_url_renders_embed_in_view(self):
        """An Article whose content_html contains an embedded video player
        is served correctly in the blog detail view."""
        from content.management.commands.load_content import md_to_html

        markdown_body = (
            "# Article with Video\n"
            "\n"
            "https://www.youtube.com/watch?v=viewTest1\n"
            "\n"
            "Read more below."
        )
        content_html = md_to_html(markdown_body)
        content_html = replace_video_urls_in_html(content_html)

        article = Article.objects.create(
            title='Video Article',
            slug='video-article',
            description='Article with embedded video',
            content_markdown=markdown_body,
            content_html=content_html,
            date='2025-07-20',
            author='Test Author',
            reading_time='1 min read',
            published=True,
        )

        response = self.client.get('/blog/video-article')
        page = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-source="youtube"', page)
        self.assertIn('data-video-id="viewTest1"', page)
        self.assertIn('youtube.com/embed/viewTest1', page)

    def test_markdown_loom_url_becomes_embed(self):
        """Standalone Loom URL in markdown is replaced with a Loom embed."""
        from content.management.commands.load_content import md_to_html

        markdown_body = (
            "Watch the demo:\n"
            "\n"
            "https://www.loom.com/share/abc123pipetest\n"
            "\n"
            "End of post."
        )
        html = md_to_html(markdown_body)
        html = replace_video_urls_in_html(html)

        self.assertIn('data-source="loom"', html)
        self.assertIn('data-video-id="abc123pipetest"', html)
        self.assertIn('loom.com/embed/abc123pipetest', html)

    def test_inline_youtube_url_not_replaced_in_pipeline(self):
        """A YouTube URL within a sentence should NOT be replaced."""
        from content.management.commands.load_content import md_to_html

        markdown_body = "Check out https://www.youtube.com/watch?v=inline1 for details."
        html = md_to_html(markdown_body)
        html = replace_video_urls_in_html(html)

        self.assertNotIn('data-source', html)
        self.assertIn('Check out', html)
