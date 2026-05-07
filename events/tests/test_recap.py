"""Tests for content-repo driven event recap pages."""

import os
import tempfile

from django.test import TestCase
from django.utils import timezone

from events.models import Event


class EventRecapModelTest(TestCase):
    def test_has_recap_uses_rendered_recap_html(self):
        event = Event.objects.create(
            title='Rendered Recap',
            slug='rendered-recap',
            start_datetime=timezone.now(),
        )
        self.assertFalse(event.has_recap)

        event.recap_html = '<h2>Published recap</h2>'
        event.save()
        self.assertTrue(event.has_recap)


class EventRecapViewTest(TestCase):
    def test_event_detail_renders_recap_html_in_place_of_description(self):
        # Issue #426 retired the inline recording block. The recap HTML
        # supplied via content sync replaces the description on completed
        # events, so the recording embed (when present) is now part of the
        # recap markup itself rather than a templated inline block.
        Event.objects.create(
            title='Launch',
            slug='launch',
            description='Original launch description',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://www.youtube.com/watch?v=test',
            timestamps=[{'time_seconds': 0, 'label': 'Intro'}],
            recap_html='<h2>Watch the recording</h2>',
        )
        response = self.client.get('/events/launch')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_detail.html')
        self.assertContains(response, 'Launch')
        self.assertContains(response, 'Watch the recording')
        # Inline recording UI is gone — recordings live on the workshop
        # video page now.
        self.assertNotContains(response, 'data-testid="event-recording-block"')
        content = response.content.decode()
        # The original description is suppressed when recap_html is present.
        self.assertNotIn(
            '<p class="text-muted-foreground">Original launch description</p>',
            content,
        )

    def test_event_detail_omits_recap_html_when_absent(self):
        Event.objects.create(
            title='No Recap',
            slug='no-recap',
            start_datetime=timezone.now(),
            status='completed',
        )
        response = self.client.get('/events/no-recap')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Watch the recording')

    def test_old_recap_url_returns_404(self):
        Event.objects.create(
            title='Launch',
            slug='launch',
            start_datetime=timezone.now(),
            status='completed',
            recap_html='<h2>Watch the recording</h2>',
        )
        response = self.client.get('/events/launch/recap')
        self.assertEqual(response.status_code, 404)


class EventDetailRecapLinkTest(TestCase):
    def test_upcoming_event_with_synced_recap_still_shows_normal_event_page(self):
        Event.objects.create(
            title='Has Recap',
            slug='has-recap',
            start_datetime=timezone.now(),
            status='upcoming',
            recap_html='<h2>Summary</h2>',
        )
        response = self.client.get('/events/has-recap')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Issue #484: anonymous CTA was rewritten to lead with the
        # account requirement and explain the newsletter/email implications.
        self.assertIn('A free account is required to register', content)
        self.assertNotIn('<h2>Summary</h2>', content)
        self.assertNotIn('View event recap', content)
        self.assertNotIn('/events/has-recap/recap', content)

    def test_event_detail_hides_recap_link_when_no_rendered_recap(self):
        Event.objects.create(
            title='No Recap',
            slug='no-recap-event',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        response = self.client.get('/events/no-recap-event')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('View event recap', content)
        self.assertNotIn('/events/no-recap-event/recap', content)


class SyncEventsRecapFileTest(TestCase):
    def _make_source(self):
        from integrations.models import ContentSource

        return ContentSource.objects.create(repo_name='test-content')

    def _write(self, root, rel_path, contents):
        path = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(contents)

    def test_sync_renders_recap_file_and_repo_include(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/launch.yaml', (
                'content_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Launch"\n'
                'slug: launch\n'
                'status: completed\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recording_embed_url: "https://www.youtube.com/embed/test-video"\n'
                'recap_file: launch/recap.md\n'
            ))
            self._write(tmp, 'events/launch/recap.md', (
                '---\n'
                'cta_label: "Start building"\n'
                'topics:\n'
                '  - title: "Execution"\n'
                '    summary: "Ship real projects."\n'
                '---\n'
                '# Recap\n\n'
                '<!-- include:recording.html -->\n\n'
                '<!-- include:topics.html -->\n'
            ))
            self._write(tmp, 'events/launch/recording.html', (
                '<section id="watch-stream">\n'
                '  <h2>Watch the recording</h2>\n'
                '  <iframe src="{{ event.recording_embed_url }}"></iframe>\n'
                '</section>\n'
            ))
            self._write(tmp, 'events/launch/topics.html', (
                '<section id="topics">\n'
                '  {% for topic in data.topics %}\n'
                '  <article><h3>{{ topic.title }}</h3><p>{{ topic.summary }}</p></article>\n'
                '  {% endfor %}\n'
                '  <a href="/pricing">{{ data.cta_label }}</a>\n'
                '</section>\n'
            ))

            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(sync_log.errors, [])
        event = Event.objects.get(slug='launch')
        self.assertEqual(event.recap_file, 'launch/recap.md')
        self.assertIn('# Recap', event.recap_markdown)
        self.assertEqual(event.recap_data['cta_label'], 'Start building')
        self.assertEqual(
            event.recording_embed_url,
            'https://www.youtube.com/embed/test-video',
        )
        self.assertIn('id="watch-stream"', event.recap_html)
        self.assertIn('Watch the recording', event.recap_html)
        self.assertIn('youtube.com/embed/test-video', event.recap_html)
        self.assertIn('Execution', event.recap_html)
        self.assertIn('Ship real projects.', event.recap_html)
        self.assertIn('Start building', event.recap_html)
        self.assertNotIn('<!-- include:', event.recap_html)
        self.assertTrue(event.has_recap)

    def test_sync_without_recap_file_leaves_rendered_recap_empty(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/no-recap.yaml', (
                'content_id: bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "No Recap"\n'
                'slug: no-recap\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
            ))
            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(sync_log.errors, [])
        event = Event.objects.get(slug='no-recap')
        self.assertEqual(event.recap_file, '')
        self.assertEqual(event.recap_html, '')
        self.assertFalse(event.has_recap)

    def test_sync_removing_recap_file_clears_rendered_recap_fields(self):
        from integrations.services.github import sync_content_source

        source = self._make_source()
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/launch.yaml', (
                'content_id: dddddddd-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Launch"\n'
                'slug: launch\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: launch/recap.md\n'
            ))
            self._write(tmp, 'events/launch/recap.md', '# Recap\n\nRendered.')
            sync_content_source(source, repo_dir=tmp)

            self._write(tmp, 'events/launch.yaml', (
                'content_id: dddddddd-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Launch"\n'
                'slug: launch\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
            ))
            os.remove(os.path.join(tmp, 'events/launch/recap.md'))
            sync_log = sync_content_source(source, repo_dir=tmp)

        self.assertEqual(sync_log.errors, [])
        event = Event.objects.get(slug='launch')
        self.assertEqual(event.recap_file, '')
        self.assertEqual(event.recap_markdown, '')
        self.assertEqual(event.recap_html, '')
        self.assertEqual(event.recap_data, {})
        self.assertFalse(event.has_recap)

    def test_sync_logs_error_for_missing_include(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/bad.yaml', (
                'content_id: cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Bad"\n'
                'slug: bad\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: bad/recap.md\n'
            ))
            self._write(tmp, 'events/bad/recap.md', (
                '# Bad\n\n<!-- include:missing.html -->\n'
            ))
            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(len(sync_log.errors), 1)
        self.assertIn('Include file not found', sync_log.errors[0]['error'])
        event = Event.objects.get(slug='bad')
        self.assertEqual(event.title, 'Bad')
        self.assertEqual(event.recap_file, 'bad/recap.md')
        self.assertEqual(event.recap_html, '')
        self.assertFalse(event.has_recap)

    def test_sync_logs_error_for_absolute_recap_file(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/bad-path.yaml', (
                'content_id: eeeeeeee-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Bad Path"\n'
                'slug: bad-path\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: /tmp/recap.md\n'
            ))
            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(len(sync_log.errors), 1)
        self.assertIn('recap_file must be relative', sync_log.errors[0]['error'])
        event = Event.objects.get(slug='bad-path')
        self.assertEqual(event.recap_html, '')
        self.assertFalse(event.has_recap)

    def test_sync_logs_error_for_escaping_recap_file(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/bad-path.yaml', (
                'content_id: ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Bad Path"\n'
                'slug: bad-path\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: ../../recap.md\n'
            ))
            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(len(sync_log.errors), 1)
        self.assertIn('recap_file escapes content repo', sync_log.errors[0]['error'])
        event = Event.objects.get(slug='bad-path')
        self.assertEqual(event.recap_html, '')
        self.assertFalse(event.has_recap)

    def test_sync_logs_error_for_invalid_recap_frontmatter(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/bad-data.yaml', (
                'content_id: 11111111-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Bad Data"\n'
                'slug: bad-data\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: bad-data/recap.md\n'
            ))
            self._write(tmp, 'events/bad-data/recap.md', (
                '---\n'
                'hero: [unterminated\n'
                '---\n'
                '# Recap\n'
            ))
            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(len(sync_log.errors), 1)
        self.assertIn('Failed to parse frontmatter', sync_log.errors[0]['error'])
        event = Event.objects.get(slug='bad-data')
        self.assertEqual(event.recap_html, '')
        self.assertFalse(event.has_recap)

    def test_sync_logs_error_for_escaping_include_path(self):
        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/bad-include.yaml', (
                'content_id: 22222222-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Bad Include"\n'
                'slug: bad-include\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: bad-include/recap.md\n'
            ))
            self._write(tmp, 'events/bad-include/recap.md', (
                '# Recap\n\n<!-- include:../../../outside.html -->\n'
            ))
            sync_log = sync_content_source(self._make_source(), repo_dir=tmp)

        self.assertEqual(len(sync_log.errors), 1)
        self.assertIn('Include path escapes content repo', sync_log.errors[0]['error'])
        event = Event.objects.get(slug='bad-include')
        self.assertEqual(event.recap_html, '')
        self.assertFalse(event.has_recap)
