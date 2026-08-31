"""Tests for event recaps: Studio-authored notes and content-repo recaps."""

import os
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
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

    def test_save_renders_recap_notes_markdown_to_html(self):
        event = Event.objects.create(
            title='Notes Only',
            slug='notes-only',
            start_datetime=timezone.now(),
            recap_notes='## Week 1\n\nWe covered batching.',
        )
        event.refresh_from_db()
        self.assertIn('<h2>Week 1</h2>', event.recap_notes_html)
        self.assertIn('We covered batching.', event.recap_notes_html)

    def test_studio_notes_take_precedence_over_synced_recap(self):
        event = Event.objects.create(
            title='Both Sources',
            slug='both-sources',
            start_datetime=timezone.now(),
            recap_file='launch/recap.md',
            recap_html='<h2>Synced recap</h2>',
            recap_notes='Organiser notes for this session',
        )
        self.assertIn('Organiser notes for this session', event.recap_body_html)
        self.assertNotIn('Synced recap', event.recap_body_html)

        # Clearing the notes restores the synced recap — it was never deleted.
        event.recap_notes = ''
        event.save()
        self.assertEqual(event.recap_body_html, '<h2>Synced recap</h2>')

    def test_has_recap_true_for_notes_only_and_for_synced_only(self):
        notes_only = Event.objects.create(
            title='Notes Only Event',
            slug='notes-only-event',
            start_datetime=timezone.now(),
            recap_notes='Some notes.',
        )
        synced_only = Event.objects.create(
            title='Synced Only Event',
            slug='synced-only-event',
            start_datetime=timezone.now(),
            recap_file='x/recap.md',
            recap_html='<h2>Synced</h2>',
        )
        self.assertTrue(notes_only.has_recap)
        self.assertTrue(synced_only.has_recap)

    def test_recap_is_published_requires_recap_and_past_event(self):
        upcoming = Event.objects.create(
            title='Upcoming With Notes',
            slug='upcoming-with-notes',
            start_datetime=timezone.now() + timedelta(days=3),
            recap_notes='Draft notes.',
        )
        past_without_recap = Event.objects.create(
            title='Past Without Recap',
            slug='past-without-recap',
            start_datetime=timezone.now() - timedelta(hours=5),
            end_datetime=timezone.now() - timedelta(hours=4),
            status='completed',
        )
        past_with_recap = Event.objects.create(
            title='Past With Notes',
            slug='past-with-notes',
            start_datetime=timezone.now() - timedelta(hours=5),
            end_datetime=timezone.now() - timedelta(hours=4),
            status='completed',
            recap_notes='Published notes.',
        )
        self.assertFalse(upcoming.recap_is_published)
        self.assertFalse(past_without_recap.recap_is_published)
        self.assertTrue(past_with_recap.recap_is_published)

    def test_get_recap_url_shape_and_empty_contract(self):
        event = Event.objects.create(
            title='Recap URL',
            slug='recap-url',
            start_datetime=timezone.now() - timedelta(hours=5),
            end_datetime=timezone.now() - timedelta(hours=4),
            recap_notes='Notes.',
        )
        self.assertEqual(
            event.get_recap_url(),
            f'/events/{event.pk}/recap-url/recap',
        )

        no_recap = Event.objects.create(
            title='No Recap URL',
            slug='no-recap-url',
            start_datetime=timezone.now(),
        )
        self.assertEqual(no_recap.get_recap_url(), '')

        unsaved = Event(
            title='Unsaved',
            slug='unsaved',
            start_datetime=timezone.now(),
            recap_notes_html='<p>Notes.</p>',
        )
        self.assertEqual(unsaved.get_recap_url(), '')

    def test_get_recap_url_available_before_the_event_ends_for_preview(self):
        # Issue #1458: the URL helper deliberately does NOT gate on
        # ``is_past`` so Studio can offer staff a preview link.
        upcoming = Event.objects.create(
            title='Preview Me',
            slug='preview-me',
            start_datetime=timezone.now() + timedelta(days=2),
            recap_notes='Draft notes.',
        )
        self.assertFalse(upcoming.recap_is_published)
        self.assertTrue(upcoming.get_recap_url().endswith('/recap'))


class EventRecapPageViewTest(TestCase):
    """Issue #1458 — dedicated recap route (reverses the #393 decision).

    #393 retired the ``/recap`` sub-route in favour of rendering the recap
    inline on the event detail page. That call predates the id-canonical
    event URLs (#673) and the book club's weekly recap cadence; the owner
    approved reversing it, so the assertions below intentionally replace
    the old "404 / inline" expectations.
    """

    @staticmethod
    def _past_event(**kwargs):
        defaults = {
            'title': 'Book Club Kickoff',
            'slug': 'book-club-kickoff',
            'description': 'Weekly book club meeting.',
            'start_datetime': timezone.now() - timedelta(hours=3),
            'end_datetime': timezone.now() - timedelta(hours=1),
            'status': 'completed',
        }
        defaults.update(kwargs)
        return Event.objects.create(**defaults)

    def test_studio_notes_render_on_the_recap_page(self):
        event = self._past_event(recap_notes='## What we covered\n\nBatching.')
        response = self.client.get(event.get_recap_url())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_recap.html')
        self.assertContains(response, '<h2>What we covered</h2>', html=False)
        self.assertContains(response, 'Batching.')
        self.assertContains(response, 'data-testid="event-recap-notes-body"')

    def test_synced_recap_renders_on_the_recap_page(self):
        event = self._past_event(
            slug='launch',
            recap_file='launch/recap.md',
            recap_html='<h2>Watch the recording</h2>',
        )
        response = self.client.get(event.get_recap_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Watch the recording')
        self.assertContains(response, 'data-testid="event-recap-synced-body"')

    def test_recap_page_404s_without_a_recap_body(self):
        event = self._past_event(slug='no-recap-at-all')
        response = self.client.get(f'/events/{event.pk}/no-recap-at-all/recap')
        self.assertEqual(response.status_code, 404)

    def test_unpublished_recap_redirects_anonymous_visitor_to_the_event(self):
        event = Event.objects.create(
            title='Upcoming Book Club',
            slug='upcoming-book-club',
            start_datetime=timezone.now() + timedelta(days=4),
            status='upcoming',
            recap_notes='Draft notes nobody should see yet.',
        )
        response = self.client.get(event.get_recap_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], event.get_absolute_url())

    def test_unpublished_recap_renders_for_staff_with_a_notice(self):
        event = Event.objects.create(
            title='Upcoming Book Club',
            slug='upcoming-book-club',
            start_datetime=timezone.now() + timedelta(days=4),
            status='upcoming',
            recap_notes='Draft notes for staff preview.',
        )
        staff = get_user_model().objects.create_user(
            email='recap-staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(event.get_recap_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="event-recap-unpublished-notice"',
        )
        self.assertContains(response, 'Not visible to members yet')
        self.assertContains(response, 'Draft notes for staff preview.')

    def test_wrong_slug_redirects_to_the_canonical_recap_url(self):
        event = self._past_event(recap_notes='Notes.')
        response = self.client.get(
            f'/events/{event.pk}/stale-old-slug/recap',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], event.get_recap_url())

    def test_draft_recap_404s_for_anonymous_and_renders_for_staff(self):
        event = self._past_event(
            slug='draft-recap', status='draft', recap_notes='Draft-only notes.',
        )
        anon_response = self.client.get(event.get_recap_url())
        self.assertEqual(anon_response.status_code, 404)

        staff = get_user_model().objects.create_user(
            email='draft-recap-staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        staff_response = self.client.get(event.get_recap_url())
        self.assertEqual(staff_response.status_code, 200)
        self.assertContains(staff_response, 'Draft-only notes.')

    def test_recap_page_canonical_link_and_no_event_json_ld(self):
        event = self._past_event(recap_notes='Notes.')
        response = self.client.get(event.get_recap_url())
        content = response.content.decode()
        self.assertIn(
            f'<link rel="canonical" href="{response.context["site_url"]}'
            f'{event.get_recap_url()}">',
            content,
        )
        # The Event JSON-LD entity stays on the detail page only, so the two
        # URLs never compete as Event entities.
        self.assertNotIn('"@type": "Event"', content)


class EventRecapLegacyUrlTest(TestCase):
    """Issue #1458: ``/events/<slug>/recap`` 301s instead of 404ing.

    This replaces ``test_old_recap_url_returns_404``, which encoded the
    superseded #393 decision.
    """

    def test_legacy_recap_url_redirects_to_the_canonical_recap_url(self):
        event = Event.objects.create(
            title='Launch',
            slug='launch',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
            recap_html='<h2>Watch the recording</h2>',
        )
        response = self.client.get('/events/launch/recap')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], event.get_recap_url())

    def test_legacy_recap_url_without_a_recap_redirects_to_event_detail(self):
        event = Event.objects.create(
            title='No Recap',
            slug='no-recap',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
        )
        response = self.client.get('/events/no-recap/recap')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], event.get_absolute_url())

    def test_legacy_recap_url_404s_for_an_unknown_slug(self):
        response = self.client.get('/events/never-existed/recap')
        self.assertEqual(response.status_code, 404)


class EventDetailRecapCtaTest(TestCase):
    """Issue #1458 — event detail links to the recap instead of inlining it.

    Replaces ``EventDetailRecapLinkTest`` and
    ``test_event_detail_renders_recap_html_in_place_of_description``, which
    both asserted the superseded #393 inline-only behaviour.
    """

    def test_past_event_shows_its_own_description_and_a_recap_cta(self):
        event = Event.objects.create(
            title='Launch',
            slug='launch',
            description='Original launch description',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
            recap_html='<h2>Watch the recording</h2>',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The description is no longer suppressed by a recap.
        self.assertIn('Original launch description', content)
        # The recap body itself moved to its own URL.
        self.assertNotIn('<h2>Watch the recording</h2>', content)
        self.assertIn('data-testid="event-recap-cta"', content)
        self.assertIn('Read the recap', content)
        self.assertIn(event.get_recap_url(), content)

    def test_upcoming_event_with_a_recap_shows_no_cta_and_no_recap_body(self):
        event = Event.objects.create(
            title='Has Recap',
            slug='has-recap',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
            recap_html='<h2>Summary</h2>',
            recap_notes='Draft notes nobody should see yet.',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Issue #513: anonymous CTA on free events is the inline email form.
        self.assertIn('event-anonymous-email-form', content)
        self.assertNotIn('<h2>Summary</h2>', content)
        self.assertNotIn('Draft notes nobody should see yet.', content)
        self.assertNotIn('data-testid="event-recap-cta"', content)
        self.assertNotIn('Read the recap', content)

    def test_event_without_a_recap_shows_no_cta_and_no_placeholder(self):
        event = Event.objects.create(
            title='No Recap',
            slug='no-recap-event',
            description='Just a description.',
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            status='completed',
        )
        response = self.client.get(event.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('data-testid="event-recap-cta"', content)
        self.assertNotIn('Read the recap', content)
        self.assertNotIn('recap coming soon', content.lower())


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
                '  <a href="/membership">{{ data.cta_label }}</a>\n'
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

    def test_sync_never_touches_studio_authored_recap_notes(self):
        # Issue #1458: ``recap_notes`` / ``recap_notes_html`` are not part of
        # the sync defaults, so re-syncing an event that has Studio notes
        # must leave both byte-identical.
        from integrations.services.github import sync_content_source

        source = self._make_source()
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'events/launch.yaml', (
                'content_id: 33333333-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                'title: "Launch"\n'
                'slug: launch\n'
                'start_datetime: "2026-04-13T16:30:00Z"\n'
                'recap_file: launch/recap.md\n'
            ))
            self._write(tmp, 'events/launch/recap.md', '# Recap\n\nSynced.')
            sync_content_source(source, repo_dir=tmp)

            event = Event.objects.get(slug='launch')
            event.recap_notes = '## Organiser notes\n\nStudio wrote this.'
            event.save()
            notes_html = Event.objects.get(pk=event.pk).recap_notes_html

            sync_log = sync_content_source(source, repo_dir=tmp)

        self.assertEqual(sync_log.errors, [])
        event.refresh_from_db()
        self.assertEqual(
            event.recap_notes, '## Organiser notes\n\nStudio wrote this.',
        )
        self.assertEqual(event.recap_notes_html, notes_html)
        # The synced pair is still there, just shadowed by the notes.
        self.assertIn('Synced.', event.recap_html)
        self.assertIn('Studio wrote this.', event.recap_body_html)
        self.assertNotIn('Synced.', event.recap_body_html)

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

        self.assertEqual(sync_log.status, 'failed')
        self.assertEqual(sync_log.errors[0]['step'], 'filesystem_boundary')
        self.assertEqual(sync_log.errors[0]['kind'], 'absolute_path')
        self.assertFalse(Event.objects.filter(slug='bad-path').exists())

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

        self.assertEqual(sync_log.status, 'failed')
        self.assertEqual(sync_log.errors[0]['step'], 'filesystem_boundary')
        self.assertEqual(sync_log.errors[0]['kind'], 'outside_checkout')
        self.assertFalse(Event.objects.filter(slug='bad-path').exists())

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

        self.assertEqual(sync_log.status, 'failed')
        self.assertEqual(sync_log.errors[0]['step'], 'filesystem_boundary')
        self.assertEqual(sync_log.errors[0]['kind'], 'outside_checkout')
        self.assertFalse(Event.objects.filter(slug='bad-include').exists())
