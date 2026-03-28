"""Tests for Events calendar view - issue #187.

Covers:
- Calendar renders current month with correct heading
- Calendar shows events on the correct day
- Month navigation links are correct
- Draft events are excluded
- Today is highlighted in the grid
- Specific month URL works
- View toggle links appear on both list and calendar pages
- Empty month shows appropriate message
- Tier badges appear on gated events
- Anonymous access (no login required)
"""

from django.test import TestCase
from django.utils import timezone

from events.models import Event


class CalendarViewTest(TestCase):
    """Tests for the events calendar view."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='AI Workshop',
            slug='ai-workshop',
            start_datetime=timezone.make_aware(
                timezone.datetime(2026, 3, 15, 14, 0)
            ),
            status='upcoming',
            event_type='live',
        )
        cls.draft_event = Event.objects.create(
            title='Draft Event',
            slug='draft-event',
            start_datetime=timezone.make_aware(
                timezone.datetime(2026, 3, 20, 10, 0)
            ),
            status='draft',
            event_type='live',
        )
        cls.gated_event = Event.objects.create(
            title='Advanced AI Agents',
            slug='advanced-ai-agents',
            start_datetime=timezone.make_aware(
                timezone.datetime(2026, 3, 20, 16, 0)
            ),
            status='upcoming',
            event_type='live',
            required_level=20,
        )

    def test_calendar_renders_specific_month(self):
        response = self.client.get('/events/calendar/2026/3')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'March 2026')
        self.assertTemplateUsed(response, 'events/events_calendar.html')

    def test_calendar_renders_current_month(self):
        response = self.client.get('/events/calendar')
        self.assertEqual(response.status_code, 200)
        today = timezone.now()
        import calendar
        month_name = calendar.month_name[today.month]
        self.assertContains(response, f'{month_name} {today.year}')

    def test_calendar_shows_events(self):
        response = self.client.get('/events/calendar/2026/3')
        self.assertContains(response, 'AI Workshop')
        # Event should be in the context weeks
        weeks = response.context['weeks']
        day_15_found = False
        for week in weeks:
            for cell in week:
                if cell['day'] == 15:
                    self.assertEqual(len(cell['events']), 1)
                    self.assertEqual(cell['events'][0].title, 'AI Workshop')
                    day_15_found = True
        self.assertTrue(day_15_found, 'Day 15 not found in calendar grid')

    def test_calendar_excludes_draft_events(self):
        response = self.client.get('/events/calendar/2026/3')
        self.assertNotContains(response, 'Draft Event')
        # Verify draft event is not in any day cell
        weeks = response.context['weeks']
        for week in weeks:
            for cell in week:
                for event in cell['events']:
                    self.assertNotEqual(event.title, 'Draft Event')

    def test_calendar_month_navigation(self):
        response = self.client.get('/events/calendar/2026/3')
        # Previous month link (February 2026)
        self.assertContains(response, '/events/calendar/2026/2')
        # Next month link (April 2026)
        self.assertContains(response, '/events/calendar/2026/4')

    def test_calendar_navigation_year_boundary_december(self):
        response = self.client.get('/events/calendar/2026/12')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'December 2026')
        # Next month should be January 2027
        self.assertContains(response, '/events/calendar/2027/1')
        # Previous month should be November 2026
        self.assertContains(response, '/events/calendar/2026/11')

    def test_calendar_navigation_year_boundary_january(self):
        response = self.client.get('/events/calendar/2026/1')
        self.assertEqual(response.status_code, 200)
        # Previous month should be December 2025
        self.assertContains(response, '/events/calendar/2025/12')
        # Next month should be February 2026
        self.assertContains(response, '/events/calendar/2026/2')

    def test_calendar_today_highlighted(self):
        today = timezone.now().date()
        response = self.client.get(
            f'/events/calendar/{today.year}/{today.month}'
        )
        weeks = response.context['weeks']
        today_found = False
        for week in weeks:
            for cell in week:
                if cell['day'] == today.day:
                    self.assertTrue(
                        cell['is_today'],
                        'Today cell should have is_today=True',
                    )
                    today_found = True
                elif cell['day'] != 0:
                    self.assertFalse(cell['is_today'])
        self.assertTrue(today_found, 'Today not found in calendar grid')

    def test_calendar_empty_month_message(self):
        response = self.client.get('/events/calendar/2025/1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No events scheduled this month.')

    def test_view_toggle_on_calendar_page(self):
        response = self.client.get('/events/calendar/2026/3')
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/events/calendar"')

    def test_view_toggle_on_list_page(self):
        response = self.client.get('/events')
        self.assertContains(response, 'href="/events/calendar"')

    def test_tier_badge_on_gated_event(self):
        response = self.client.get('/events/calendar/2026/3')
        self.assertContains(response, 'Advanced AI Agents')
        # The gated event should show a tier badge with lock icon
        content = response.content.decode()
        # Check that the tier badge markup appears near the gated event
        self.assertIn('data-lucide="lock"', content)

    def test_event_links_to_detail_page(self):
        response = self.client.get('/events/calendar/2026/3')
        self.assertContains(response, '/events/ai-workshop')

    def test_anonymous_can_view_calendar(self):
        # No login - should still get 200
        response = self.client.get('/events/calendar/2026/3')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI Workshop')

    def test_multiple_events_same_day(self):
        Event.objects.create(
            title='Coding Session',
            slug='coding-session',
            start_datetime=timezone.make_aware(
                timezone.datetime(2026, 3, 15, 18, 0)
            ),
            status='upcoming',
            event_type='async',
        )
        response = self.client.get('/events/calendar/2026/3')
        weeks = response.context['weeks']
        for week in weeks:
            for cell in week:
                if cell['day'] == 15:
                    self.assertEqual(len(cell['events']), 2)

    def test_today_button_links_to_current_month(self):
        response = self.client.get('/events/calendar/2025/1')
        self.assertContains(response, 'Today')
        # Today button links to /events/calendar (no year/month = current)
        self.assertContains(response, 'href="/events/calendar"')

    def test_context_has_expected_keys(self):
        response = self.client.get('/events/calendar/2026/3')
        ctx = response.context
        self.assertIn('weeks', ctx)
        self.assertIn('month_name', ctx)
        self.assertIn('year', ctx)
        self.assertIn('month', ctx)
        self.assertIn('prev_year', ctx)
        self.assertIn('prev_month', ctx)
        self.assertIn('next_year', ctx)
        self.assertIn('next_month', ctx)
        self.assertEqual(ctx['month_name'], 'March')
        self.assertEqual(ctx['year'], 2026)
        self.assertEqual(ctx['month'], 3)

    def test_agenda_days_for_mobile(self):
        response = self.client.get('/events/calendar/2026/3')
        agenda_days = response.context['agenda_days']
        # Should have days with events only
        day_numbers = [d['day'] for d in agenda_days]
        self.assertIn(15, day_numbers)  # AI Workshop
        self.assertIn(20, day_numbers)  # Advanced AI Agents (gated, non-draft)
        # Draft event on day 20 should not appear
        for day_info in agenda_days:
            for event in day_info['events']:
                self.assertNotEqual(event.title, 'Draft Event')
