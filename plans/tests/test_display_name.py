"""Tests for the ``display_name`` helper and template filter (issue #440).

The helper is the single source of truth for "what name do we show for
this user?" -- ``first_name + last_name`` if any name is populated,
otherwise the email local-part. Whitespace-only names count as empty.
``None`` returns the empty string so templates can render without an
``{% if user %}`` guard.
"""

import datetime
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from accounts.utils.display import display_name
from plans.models import Plan, Sprint

User = get_user_model()


class _TestIdTextParser(HTMLParser):
    def __init__(self, testid):
        super().__init__()
        self.testid = testid
        self.values = []
        self._depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if self._depth:
            self._depth += 1
            return
        if dict(attrs).get('data-testid') == self.testid:
            self._depth = 1
            self._parts = []

    def handle_endtag(self, tag):
        if not self._depth:
            return
        self._depth -= 1
        if not self._depth:
            self.values.append(''.join(self._parts).strip())
            self._parts = []

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)


def _texts_for_testid(html, testid):
    parser = _TestIdTextParser(testid)
    parser.feed(html)
    return parser.values


class DisplayNameHelperTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.first_and_last = User.objects.create_user(
            email='cp@test.com', password='pw',
            first_name='Carlos', last_name='Pumar',
        )
        cls.first_only = User.objects.create_user(
            email='luca@test.com', password='pw',
            first_name='Luca', last_name='',
        )
        cls.last_only = User.objects.create_user(
            email='smith@test.com', password='pw',
            first_name='', last_name='Smith',
        )
        cls.email_handle = User.objects.create_user(
            email='ada@example.com', password='pw',
            first_name='', last_name='',
        )
        cls.whitespace_only = User.objects.create_user(
            email='bob@example.com', password='pw',
            first_name='  ', last_name='  ',
        )

    def test_display_name_first_and_last(self):
        self.assertEqual(display_name(self.first_and_last), 'Carlos Pumar')

    def test_display_name_first_only(self):
        self.assertEqual(display_name(self.first_only), 'Luca')

    def test_display_name_last_only(self):
        self.assertEqual(display_name(self.last_only), 'Smith')

    def test_display_name_email_local_part_fallback(self):
        self.assertEqual(display_name(self.email_handle), 'ada')

    def test_display_name_handles_whitespace(self):
        """``first='  ', last='  '`` falls through to the email handle."""
        self.assertEqual(display_name(self.whitespace_only), 'bob')

    def test_display_name_none_user(self):
        self.assertEqual(display_name(None), '')


class DisplayNameTemplateFilterTest(TestCase):
    def test_filter_renders_named_user(self):
        user = User.objects.create_user(
            email='cp@test.com', password='pw',
            first_name='Carlos', last_name='Pumar',
        )
        rendered = Template(
            '{% load accounts_extras %}{{ user|display_name }}',
        ).render(Context({'user': user}))
        self.assertEqual(rendered, 'Carlos Pumar')

    def test_filter_renders_email_handle_when_no_name(self):
        user = User.objects.create_user(
            email='ada@example.com', password='pw',
            first_name='', last_name='',
        )
        rendered = Template(
            '{% load accounts_extras %}{{ user|display_name }}',
        ).render(Context({'user': user}))
        self.assertEqual(rendered, 'ada')


class DisplayNameTemplateFilterRendersInCohortBoardTest(TestCase):
    def test_both_named_and_email_only_users_render(self):
        sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        viewer = User.objects.create_user(
            email='viewer@test.com', password='pw',
        )
        Plan.objects.create(
            member=viewer, sprint=sprint, visibility='cohort',
        )
        named = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Smith',
        )
        Plan.objects.create(
            member=named, sprint=sprint, visibility='cohort',
        )
        email_only = User.objects.create_user(
            email='ada@example.com', password='pw',
            first_name='', last_name='',
        )
        Plan.objects.create(
            member=email_only, sprint=sprint, visibility='cohort',
        )
        self.client.force_login(viewer)
        url = reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # ``Alice Smith`` is the rendered display name for the named
        # user; the unique string lets us assert without false positives.
        self.assertContains(response, 'Alice Smith')
        # ``ada`` is the email-handle fallback. Scope the assertion to
        # the cohort plan card so we don't false-match a different
        # ``ada`` elsewhere on the page (there is none, but the scoped
        # assertion is still the safe form per testing Rule 2).
        seen = set(_texts_for_testid(
            response.content.decode(),
            'cohort-plan-name',
        ))
        self.assertIn('Alice Smith', seen)
        self.assertIn('ada', seen)
