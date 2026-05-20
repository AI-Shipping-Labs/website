"""Tests for the unified Studio form action bar (issue #741).

Sprints, plans, redirects, and event_series forms now all use the
shared ``studio/includes/sticky_action_bar.html`` partial instead of
inline action rows. These tests assert each migrated form renders:

- the ``sticky-save-action`` testid with the correct label,
- the ``sticky-cancel-action`` testid as a bordered button (not a
  text-link),
- a matching ``form`` id so the partial's submit button targets it,
- no remnants of the old ``px-6`` inline save or text-link cancel.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from events.models import EventSeries
from integrations.models import Redirect
from plans.models import Sprint

User = get_user_model()


class StaffClientMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')


class SprintFormActionBarTest(StaffClientMixin, TestCase):
    """Sprint create/edit forms use the unified action bar."""

    def test_new_sprint_form_renders_unified_action_bar(self):
        response = self.client.get('/studio/sprints/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'data-testid="sticky-cancel-action"')
        self.assertContains(response, 'form="sprint-edit-form"')
        self.assertContains(response, 'id="sprint-edit-form"')
        # Save label is "Create sprint" on the new-sprint form.
        self.assertContains(response, '<span>Create sprint</span>')

    def test_edit_sprint_form_save_label_is_save_changes(self):
        sprint = Sprint.objects.create(
            name='Edit me', slug='edit-me',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status='draft',
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, '<span>Save changes</span>')

    def test_sprint_form_cancel_is_bordered_button_not_text_link(self):
        response = self.client.get('/studio/sprints/new')
        content = response.content.decode()
        # Find the cancel link and verify it has the partial's bordered
        # button classes (not the old text-link styling).
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_tag = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 100]
        self.assertIn('border', cancel_tag)
        self.assertIn('bg-secondary', cancel_tag)
        # Verify the old text-link cancel pattern is gone from this form.
        self.assertNotContains(
            response,
            'text-sm text-muted-foreground hover:text-foreground transition-colors">\n        Cancel',
        )

    def test_sprint_form_drops_old_px6_inline_pattern(self):
        """Old inline ``px-6`` save button is gone from the sprint form."""
        response = self.client.get('/studio/sprints/new')
        self.assertNotContains(
            response,
            'bg-accent text-accent-foreground px-6 py-2 rounded-lg text-sm font-medium',
        )

    def test_sprint_form_cancel_points_to_sprint_list(self):
        response = self.client.get('/studio/sprints/new')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_anchor = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 200]
        self.assertIn('href="/studio/sprints/"', cancel_anchor)

    def test_sprint_form_still_posts_successfully(self):
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Action bar test sprint',
            'slug': 'action-bar-test-sprint',
            'start_date': '2026-08-01',
            'duration_weeks': '4',
            'status': 'draft',
        })
        self.assertEqual(Sprint.objects.count(), before + 1)
        sprint = Sprint.objects.get(slug='action-bar-test-sprint')
        self.assertRedirects(response, f'/studio/sprints/{sprint.pk}/')


class PlanFormActionBarTest(StaffClientMixin, TestCase):
    """Plan create + sprint Add member forms use the unified action bar."""

    def test_new_plan_form_renders_unified_action_bar(self):
        response = self.client.get('/studio/plans/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'data-testid="sticky-cancel-action"')
        self.assertContains(response, 'form="plan-edit-form"')
        self.assertContains(response, 'id="plan-edit-form"')
        self.assertContains(response, '<span>Create plan</span>')

    def test_new_plan_form_cancel_points_to_plan_list(self):
        response = self.client.get('/studio/plans/new')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_anchor = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 200]
        self.assertIn('href="/studio/plans/"', cancel_anchor)

    def test_sprint_add_member_form_renders_add_member_label(self):
        sprint = Sprint.objects.create(
            name='Add-member host', slug='add-member-host',
            start_date=datetime.date(2026, 5, 1),
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/add-member')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, '<span>Add member</span>')

    def test_sprint_add_member_form_cancel_points_to_sprint_detail(self):
        sprint = Sprint.objects.create(
            name='Cancel host', slug='cancel-host',
            start_date=datetime.date(2026, 5, 1),
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/add-member')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_anchor = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 200]
        self.assertIn(f'href="/studio/sprints/{sprint.pk}/"', cancel_anchor)

    def test_plan_form_cancel_is_bordered_not_text_link(self):
        response = self.client.get('/studio/plans/new')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_tag = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 100]
        self.assertIn('border', cancel_tag)
        self.assertIn('bg-secondary', cancel_tag)


class RedirectFormActionBarTest(StaffClientMixin, TestCase):
    """Redirect create/edit forms use the unified action bar."""

    def test_new_redirect_form_renders_unified_action_bar(self):
        response = self.client.get('/studio/redirects/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'data-testid="sticky-cancel-action"')
        self.assertContains(response, 'form="redirect-edit-form"')
        self.assertContains(response, 'id="redirect-edit-form"')
        self.assertContains(response, '<span>Create Redirect</span>')

    def test_edit_redirect_form_save_label_is_save_changes(self):
        obj = Redirect.objects.create(
            source_path='/old', target_path='/new',
        )
        response = self.client.get(f'/studio/redirects/{obj.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span>Save Changes</span>')

    def test_redirect_form_cancel_is_bordered_not_text_link(self):
        response = self.client.get('/studio/redirects/new')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_tag = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 100]
        self.assertIn('border', cancel_tag)
        self.assertIn('bg-secondary', cancel_tag)

    def test_redirect_form_cancel_points_to_redirect_list(self):
        response = self.client.get('/studio/redirects/new')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_anchor = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 200]
        self.assertIn('href="/studio/redirects/"', cancel_anchor)

    def test_redirect_form_still_posts_successfully(self):
        response = self.client.post('/studio/redirects/new', {
            'source_path': '/from-action-bar',
            'target_path': '/to-action-bar',
            'redirect_type': '301',
            'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Redirect.objects.filter(source_path='/from-action-bar').exists(),
        )


class EventSeriesFormActionBarTest(StaffClientMixin, TestCase):
    """Event series create form uses the unified action bar."""

    def test_new_event_series_form_renders_unified_action_bar(self):
        response = self.client.get('/studio/event-series/new')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sticky-save-action"')
        self.assertContains(response, 'data-testid="sticky-cancel-action"')
        self.assertContains(response, 'form="event-series-create-form"')
        self.assertContains(response, 'id="event-series-create-form"')
        self.assertContains(response, '<span>Create series</span>')

    def test_event_series_form_old_testid_is_gone(self):
        """The old ``event-series-submit`` testid is replaced by ``sticky-save-action``."""
        response = self.client.get('/studio/event-series/new')
        self.assertNotContains(response, 'data-testid="event-series-submit"')

    def test_event_series_form_cancel_points_to_series_list(self):
        response = self.client.get('/studio/event-series/new')
        content = response.content.decode()
        cancel_pos = content.index('data-testid="sticky-cancel-action"')
        cancel_anchor = content[content.rfind('<a', 0, cancel_pos):cancel_pos + 200]
        self.assertIn('href="/studio/event-series/"', cancel_anchor)

    def test_event_series_form_still_posts_successfully(self):
        start = datetime.date.today() + datetime.timedelta(days=14)
        before = EventSeries.objects.count()
        response = self.client.post('/studio/event-series/new', {
            'name': 'Action bar series',
            'slug': '',
            'description': '',
            'start_date': start.strftime('%d/%m/%Y'),
            'start_time': '18:00',
            'duration_hours': '1.5',
            'occurrences': '2',
            'timezone': 'Europe/Berlin',
            'required_level': '0',
            'kind': 'standard',
            'platform': 'custom',
        })
        # 302 redirect to series detail on success.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventSeries.objects.count(), before + 1)
