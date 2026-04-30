"""Tests for studio workshop management views (issue #297).

Covers the four staff-only views (list, detail, edit, re-sync) plus the
sidebar entry, the read-only display of yaml-sourced fields, and the
three-gate invariant enforcement on the edit form.
"""

import datetime
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Workshop, WorkshopPage
from events.models import Event
from integrations.models import ContentSource, SyncLog

User = get_user_model()


def _make_workshop(slug='demo', title='Demo Workshop', **kwargs):
    """Factory that lets tests provide overrides for any model field.

    Centralised so the three-gate defaults stay in sync across tests when
    the model invariant is tightened. Callers pass only the fields they
    care about; the rest get sensible defaults that satisfy
    ``landing <= pages <= recording``.
    """
    defaults = {
        'slug': slug,
        'title': title,
        'date': datetime.date(2026, 4, 21),
        'description': 'Hands-on intro.',
        'instructor_name': 'Alice',
        'tags': ['agents'],
        'status': 'published',
        'landing_required_level': 0,
        'pages_required_level': 10,
        'recording_required_level': 20,
        'cover_image_url': '',
        'code_repo_url': '',
        'source_repo': 'AI-Shipping-Labs/workshops-content',
        'source_path': f'2026/{slug}/workshop.yaml',
        'source_commit': 'abc1234def5678901234567890123456789abcde',
    }
    defaults.update(kwargs)
    return Workshop.objects.create(**defaults)


class StudioWorkshopAccessControlTest(TestCase):
    """Non-staff and anonymous users cannot reach the workshop views."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = _make_workshop()
        cls.basic_user = User.objects.create_user(
            email='basic@test.com', password='testpass', is_staff=False,
        )
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def test_anonymous_redirects_to_login(self):
        # Anonymous user gets bounced to the login page on every URL.
        for url in [
            '/studio/workshops/',
            f'/studio/workshops/{self.workshop.pk}/',
            f'/studio/workshops/{self.workshop.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_user_gets_403(self):
        self.client.login(email='basic@test.com', password='testpass')
        response = self.client.get('/studio/workshops/')
        self.assertEqual(response.status_code, 403)

        response = self.client.get(
            f'/studio/workshops/{self.workshop.pk}/edit',
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_list(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/workshops/')
        self.assertEqual(response.status_code, 200)


class StudioWorkshopListTest(TestCase):
    """Filter, search, status pill, sidebar wiring."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.published = _make_workshop(
            slug='rag-basics', title='RAG basics', status='published',
        )
        cls.draft = _make_workshop(
            slug='fine-tuning', title='Fine-tuning LLMs', status='draft',
            date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_returns_200_and_uses_template(self):
        response = self.client.get('/studio/workshops/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/workshops/list.html')

    def test_lists_all_workshops_by_default(self):
        response = self.client.get('/studio/workshops/')
        self.assertContains(response, 'RAG basics')
        self.assertContains(response, 'Fine-tuning LLMs')

    def test_filter_by_status_published(self):
        response = self.client.get('/studio/workshops/?status=published')
        self.assertContains(response, 'RAG basics')
        self.assertNotContains(response, 'Fine-tuning LLMs')

    def test_filter_by_status_draft(self):
        response = self.client.get('/studio/workshops/?status=draft')
        self.assertContains(response, 'Fine-tuning LLMs')
        self.assertNotContains(response, 'RAG basics')

    def test_search_matches_title(self):
        response = self.client.get('/studio/workshops/?q=rag')
        self.assertContains(response, 'RAG basics')
        self.assertNotContains(response, 'Fine-tuning LLMs')

    def test_search_matches_slug(self):
        response = self.client.get('/studio/workshops/?q=fine-tuning')
        self.assertContains(response, 'Fine-tuning LLMs')
        self.assertNotContains(response, 'RAG basics')

    def test_resync_button_present(self):
        response = self.client.get('/studio/workshops/')
        self.assertContains(response, 'workshop-resync-btn')
        self.assertContains(response, 'Re-sync workshops')

    def test_view_on_site_link_in_row(self):
        response = self.client.get('/studio/workshops/')
        # Public URL is /workshops/<slug> per get_absolute_url.
        self.assertContains(response, '/workshops/rag-basics')

    def test_empty_state_shown_when_no_workshops(self):
        Workshop.objects.all().delete()
        response = self.client.get('/studio/workshops/')
        self.assertContains(response, 'No workshops yet')

    def test_sidebar_entry_present(self):
        # A list page is rendered through studio/base.html, so the sidebar
        # is always part of the response.
        response = self.client.get('/studio/workshops/')
        self.assertContains(response, 'Workshops</span>')

    def test_sort_order_is_descending_by_date(self):
        response = self.client.get('/studio/workshops/')
        body = response.content.decode()
        # The May workshop should appear before the April workshop.
        idx_fine = body.find('Fine-tuning LLMs')
        idx_rag = body.find('RAG basics')
        self.assertGreater(idx_rag, 0)
        self.assertLess(idx_fine, idx_rag)

    def test_list_shows_synced_and_local_origins(self):
        local = _make_workshop(
            slug='local-workshop',
            title='Local Workshop',
            source_repo='',
            source_path='',
            source_commit='',
        )

        response = self.client.get('/studio/workshops/')

        self.assertContains(response, 'Synced')
        self.assertContains(response, '2026/rag-basics/workshop.yaml')
        self.assertContains(response, local.title)
        self.assertContains(response, 'Local / manual')
        self.assertContains(response, 'No GitHub source metadata')


class StudioWorkshopDetailTest(TestCase):
    """Detail page shows every field, the page list, and the linked event."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.event = Event.objects.create(
            slug='demo-event',
            title='Demo Event',
            kind='workshop',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://www.youtube.com/watch?v=abc123',
        )
        cls.workshop = _make_workshop(
            slug='demo', title='Demo Workshop', event=cls.event,
            cover_image_url='https://cdn.example.com/cover.png',
            code_repo_url='https://github.com/example/code',
        )
        cls.page1 = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='setup', title='Setup',
            sort_order=1, body='# Setup\n\nFirst step.',
            source_path='2026/demo/setup.md',
            source_commit='abcdef0123456789abcdef0123456789abcdef01',
        )
        cls.page2 = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='build', title='Build',
            sort_order=2, body='# Build', source_path='2026/demo/build.md',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_returns_200_and_uses_template(self):
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/workshops/detail.html')

    def test_shows_title_description_and_tags(self):
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        self.assertContains(response, 'Demo Workshop')
        self.assertContains(response, 'Hands-on intro')
        self.assertContains(response, 'agents')

    def test_shows_three_tier_gates(self):
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        # Each gate dd has a data-testid we can rely on for assertions.
        self.assertContains(response, 'data-testid="landing-gate"')
        self.assertContains(response, 'data-testid="pages-gate"')
        self.assertContains(response, 'data-testid="recording-gate"')
        # Display values from get_FOO_display.
        self.assertContains(response, 'Open (everyone)')
        self.assertContains(response, 'Basic and above')
        self.assertContains(response, 'Main and above')

    def test_shows_synced_metadata(self):
        content_id = uuid.uuid4()
        Workshop.objects.filter(pk=self.workshop.pk).update(content_id=content_id)
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        self.assertContains(response, 'AI-Shipping-Labs/workshops-content')
        self.assertContains(response, '2026/demo/workshop.yaml')
        self.assertContains(response, 'abc1234def5678901234567890123456789abcde')
        self.assertContains(response, str(content_id))
        self.assertContains(
            response,
            'https://github.com/AI-Shipping-Labs/workshops-content/'
            'blob/main/2026/demo/workshop.yaml',
        )
        self.assertContains(response, 'data-testid="resync-source-button"')

    def test_shows_linked_event_and_edit_link(self):
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        self.assertContains(response, 'Demo Event')
        self.assertContains(
            response,
            f'/studio/events/{self.event.pk}/edit',
        )

    def test_no_event_shows_dash(self):
        Workshop.objects.filter(pk=self.workshop.pk).update(event=None)
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        self.assertContains(response, 'No linked event')

    def test_lists_pages_in_sort_order(self):
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        body = response.content.decode()
        # Setup appears before Build in the rendered page table.
        self.assertLess(body.find('Setup'), body.find('Build'))

    def test_pages_have_github_source_links(self):
        self.page1.source_repo = 'AI-Shipping-Labs/workshops-content'
        self.page1.save()
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        # GitHub URL built from workshop.source_repo + page.source_path.
        self.assertContains(
            response,
            'https://github.com/AI-Shipping-Labs/workshops-content/'
            'blob/main/2026/demo/setup.md',
        )

    def test_pages_show_page_level_origin_for_synced_and_local_rows(self):
        self.page1.source_repo = 'AI-Shipping-Labs/workshops-content'
        self.page1.save()
        self.page2.source_path = ''
        self.page2.source_commit = ''
        self.page2.save()

        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')

        self.assertContains(response, '2026/demo/setup.md')
        self.assertContains(response, 'Local / manual')
        self.assertContains(response, 'No GitHub source metadata')

    def test_local_workshop_detail_has_no_github_controls(self):
        local = _make_workshop(
            slug='local-only',
            title='Local Only',
            source_repo='',
            source_path='',
            source_commit='',
        )

        response = self.client.get(f'/studio/workshops/{local.pk}/')

        self.assertContains(response, 'Local / manual')
        self.assertContains(response, 'No GitHub source metadata exists')
        self.assertNotContains(response, 'Edit on GitHub')
        self.assertNotContains(response, 'data-testid="resync-source-button"')

    def test_does_not_show_page_body(self):
        # Page body is not shown — only the source link.
        response = self.client.get(f'/studio/workshops/{self.workshop.pk}/')
        self.assertNotContains(response, 'First step')

    def test_404_for_unknown_workshop(self):
        response = self.client.get('/studio/workshops/999999/')
        self.assertEqual(response.status_code, 404)


class StudioWorkshopEditFormTest(TestCase):
    """Edit form: read-only display + 5 mutable fields + invariant."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        # Re-create per-test so mutations don't leak between tests.
        self.workshop = _make_workshop(
            slug='draft-workshop', title='Draft Workshop',
            status='draft',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )

    def test_get_returns_200(self):
        response = self.client.get(
            f'/studio/workshops/{self.workshop.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/workshops/form.html')

    def test_form_has_five_editable_inputs(self):
        response = self.client.get(
            f'/studio/workshops/{self.workshop.pk}/edit',
        )
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="cover_image_url"')
        self.assertContains(response, 'name="landing_required_level"')
        self.assertContains(response, 'name="pages_required_level"')
        self.assertContains(response, 'name="recording_required_level"')

    def test_yaml_fields_are_not_editable_inputs(self):
        # title/description/tags/date/instructor_name/code_repo_url appear on
        # the page but as <dd>/<dt>, never in form <input>/<textarea>/<select>
        # elements scoped to the form. Search the form region only — the
        # full page also contains <meta name="description" ...> which is
        # not a form input.
        response = self.client.get(
            f'/studio/workshops/{self.workshop.pk}/edit',
        )
        body = response.content.decode()
        # Slice to the editable form section.
        form_start = body.find('data-testid="workshop-edit-form"')
        form_end = body.find('</form>', form_start)
        self.assertGreater(form_start, 0)
        form_html = body[form_start:form_end]
        # No form input has any of these names.
        for fname in (
            'title', 'description', 'tags', 'instructor_name',
            'date', 'code_repo_url',
        ):
            self.assertNotIn(
                f'name="{fname}"', form_html,
                f'Form should not include an input named "{fname}"',
            )

    def test_post_valid_data_saves_and_redirects(self):
        response = self.client.post(
            f'/studio/workshops/{self.workshop.pk}/edit',
            {
                'status': 'published',
                'cover_image_url': 'https://cdn.example.com/new.png',
                'landing_required_level': '0',
                'pages_required_level': '10',
                'recording_required_level': '30',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'/studio/workshops/{self.workshop.pk}/',
        )

        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.status, 'published')
        self.assertEqual(
            self.workshop.cover_image_url, 'https://cdn.example.com/new.png',
        )
        self.assertEqual(self.workshop.recording_required_level, 30)

    def test_post_ignores_yaml_fields(self):
        # Any attempt to mutate yaml-sourced fields via the POST is silently
        # ignored — the original values must be preserved.
        original_title = self.workshop.title
        response = self.client.post(
            f'/studio/workshops/{self.workshop.pk}/edit',
            {
                'status': 'published',
                'cover_image_url': '',
                'landing_required_level': '0',
                'pages_required_level': '10',
                'recording_required_level': '20',
                'title': 'HACKED',
                'description': 'evil',
                'instructor_name': 'evil',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.title, original_title)
        self.assertNotIn('evil', self.workshop.description)

    def test_post_invariant_violation_recording_below_pages(self):
        response = self.client.post(
            f'/studio/workshops/{self.workshop.pk}/edit',
            {
                'status': 'draft',
                'cover_image_url': '',
                'landing_required_level': '0',
                'pages_required_level': '20',  # Main
                'recording_required_level': '10',  # Basic — INVALID
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recording gate must be at least')
        # Sanity: the actual model field is unchanged.
        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.recording_required_level, 20)

    def test_post_invariant_violation_landing_above_pages(self):
        response = self.client.post(
            f'/studio/workshops/{self.workshop.pk}/edit',
            {
                'status': 'draft',
                'cover_image_url': '',
                'landing_required_level': '20',  # Main — too high
                'pages_required_level': '10',     # Basic
                'recording_required_level': '20',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Landing gate must be at most')
        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.landing_required_level, 0)

    def test_invalid_form_re_renders_with_submitted_values(self):
        response = self.client.post(
            f'/studio/workshops/{self.workshop.pk}/edit',
            {
                'status': 'published',
                'cover_image_url': 'https://cdn.example.com/preview.png',
                'landing_required_level': '0',
                'pages_required_level': '20',
                'recording_required_level': '10',  # invalid
            },
        )
        # The submitted values are kept in the form so the operator can fix
        # only what's wrong without retyping the rest.
        self.assertContains(response, 'https://cdn.example.com/preview.png')


class StudioWorkshopResyncTest(TestCase):
    """Re-sync trigger fans out async_task per workshop ContentSource."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_get_is_not_allowed(self):
        response = self.client.get('/studio/workshops/resync/')
        self.assertEqual(response.status_code, 405)

    def test_no_workshop_source_flashes_error(self):
        # No ContentSource rows configured for the workshops repo.
        ContentSource.objects.filter(repo_name='AI-Shipping-Labs/workshops-content').delete()
        response = self.client.post('/studio/workshops/resync/', follow=True)
        self.assertRedirects(response, '/studio/sync/')
        # Flash message visible on the dashboard.
        self.assertContains(response, 'No content source for')
        self.assertContains(response, 'AI-Shipping-Labs/workshops-content')
        # No SyncLog rows were created.
        self.assertEqual(SyncLog.objects.count(), 0)

    def test_with_source_enqueues_async_task_and_marks_queued(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
        )
        # Patch async_task to avoid hitting django-q during the test.
        with patch('django_q.tasks.async_task') as mock_async:
            response = self.client.post(
                '/studio/workshops/resync/', follow=True,
            )

        self.assertRedirects(response, '/studio/sync/')
        self.assertContains(response, 'Workshop sync queued')
        # async_task got called once, scoped to the workshops repo.
        self.assertEqual(mock_async.call_count, 1)
        call_kwargs = mock_async.call_args.kwargs
        self.assertEqual(
            mock_async.call_args.args[0],
            'integrations.services.github.sync_content_source',
        )
        self.assertEqual(mock_async.call_args.args[1], source)
        self.assertIn('batch_id', call_kwargs)
        self.assertEqual(
            call_kwargs['task_name'],
            f'sync-{source.repo_name}',
        )

        # _mark_source_queued created a SyncLog row for the source.
        log = SyncLog.objects.get(source=source)
        self.assertEqual(log.status, 'queued')

    def test_only_workshop_sources_are_synced(self):
        # Set up sources for different repos — only the workshops one
        # must be enqueued.
        workshops = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

        with patch('django_q.tasks.async_task') as mock_async:
            self.client.post('/studio/workshops/resync/')

        self.assertEqual(mock_async.call_count, 1)
        # The single call targeted the workshops-content source.
        self.assertEqual(mock_async.call_args.args[1], workshops)

    def test_get_github_edit_url_for_workshop(self):
        from studio.utils import get_github_edit_url

        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
        )
        ws = _make_workshop(slug='demo')
        url = get_github_edit_url(ws)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/workshops-content/'
            'blob/main/2026/demo/workshop.yaml',
        )


class StudioWorkshopGetAbsoluteUrlGracefulTest(TestCase):
    """The "View on site" link must render even when the public route is missing.

    Per issue #297 the `/workshops/<slug>` URL only lights up after #296
    ships. The Studio template must not crash or call ``reverse()`` against
    a non-existent URL pattern.
    """

    def test_list_renders_absolute_url_without_reverse(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.force_login(staff)
        ws = _make_workshop(slug='demo')
        response = self.client.get('/studio/workshops/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ws.get_absolute_url())
