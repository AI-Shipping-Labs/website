"""Crawler bounds and navigation metadata for the public event calendar."""

from datetime import date
from html.parser import HTMLParser
from unittest.mock import patch

from django.test import TestCase


class CalendarNavigationParser(HTMLParser):
    """Collect labels and rel metadata for rendered calendar navigation."""

    def __init__(self):
        super().__init__()
        self.links = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        attributes = dict(attrs)
        label = attributes.get('aria-label')
        if label in {'Previous month', 'Next month'}:
            self.links[label] = attributes


class CalendarCrawlBoundsTest(TestCase):
    def test_supported_year_boundaries_render(self):
        current_year = date.today().year
        for year in (current_year - 3, current_year + 2):
            with self.subTest(year=year):
                response = self.client.get(f'/events/calendar/{year}/6')
                self.assertEqual(response.status_code, 200)

    def test_year_zero_and_out_of_window_years_return_404(self):
        current_year = date.today().year
        invalid_years = (0, current_year - 4, current_year + 3, 12000)
        for year in invalid_years:
            with self.subTest(year=year):
                response = self.client.get(f'/events/calendar/{year}/1')
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response['X-Robots-Tag'],
                    'noindex, nofollow, noarchive',
                )

    def test_invalid_month_returns_404(self):
        year = date.today().year
        for month in (0, 13):
            with self.subTest(month=month):
                response = self.client.get(f'/events/calendar/{year}/{month}')
                self.assertEqual(response.status_code, 404)

    def test_previous_and_next_links_are_nofollow(self):
        response = self.client.get('/events/calendar/2026/6')
        parser = CalendarNavigationParser()
        parser.feed(response.content.decode())

        self.assertEqual(
            parser.links['Previous month'].get('rel'), 'nofollow',
        )
        self.assertEqual(parser.links['Next month'].get('rel'), 'nofollow')

    @patch('website.robots.production_search_indexing_enabled', return_value=True)
    def test_production_robots_disallows_event_calendar(self, _mock_enabled):
        response = self.client.get('/robots.txt')
        self.assertContains(response, 'Disallow: /events/calendar/')
