"""Route-name driven Studio sidebar active state (issue #1435).

Before #1435 the sidebar derived its active section from one substring list
(``studio_sidebar_state``) and its active link from a second, independent set
of substring checks inside ``templates/studio/base.html``. Deep routes fell
through both: ``/studio/users/<id>/``, ``/studio/users/import/``,
``/studio/assistant/``, ``/studio/maven-events/``,
``/studio/questionnaire-responses/`` and ``/studio/payments/stripe-webhooks/``
rendered with their owning group collapsed, and only the Email log link ever
emitted ``aria-current``.

These tests pin the repaired contract:

- the route map partitions the whole Studio URLconf (no route can silently
  fall back to "no section" again);
- every sidebar destination owns its list/detail/form/action family;
- a page marks at most one sidebar destination current, and never a
  neighbouring link when the route has no destination of its own.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.client import RequestFactory
from django.urls import get_resolver, reverse

from studio.sidebar import (
    ROUTE_INDEX,
    ROUTES_WITHOUT_SIDEBAR_HOME,
    SECTION_ONLY_ROUTES,
    SIDEBAR_ROUTE_FAMILIES,
    sidebar_state,
)

User = get_user_model()

ANCHOR_RE = re.compile(r'<a\s[^>]*>')
HREF_RE = re.compile(r'href="([^"]*)"')


def studio_route_names():
    """Every named route under ``/studio/``."""
    names = set()

    def walk(resolver, prefix=''):
        for pattern in resolver.url_patterns:
            route = prefix + str(pattern.pattern)
            if hasattr(pattern, 'url_patterns'):
                walk(pattern, route)
            elif pattern.name and route.startswith('studio/'):
                names.add(pattern.name)

    walk(get_resolver())
    return names


class SidebarRouteMapTest(TestCase):
    """The map is complete, unambiguous, and matches the real URLconf."""

    def test_every_studio_route_is_classified_exactly_once(self):
        mapped = set(ROUTE_INDEX)
        unmapped = set(ROUTES_WITHOUT_SIDEBAR_HOME)

        self.assertEqual(
            mapped & unmapped,
            set(),
            'a route cannot both have and lack a sidebar home',
        )
        self.assertEqual(
            studio_route_names(),
            mapped | unmapped,
            'every Studio route must be mapped to a destination, mapped to a '
            'section-only owner, or listed in ROUTES_WITHOUT_SIDEBAR_HOME',
        )

    def test_no_route_is_claimed_by_two_destinations(self):
        seen = {}
        for section, destination, route_names in SIDEBAR_ROUTE_FAMILIES:
            for route_name in route_names:
                self.assertNotIn(
                    route_name,
                    seen,
                    f'{route_name} is claimed by both {seen.get(route_name)} '
                    f'and {(section, destination)}',
                )
                seen[route_name] = (section, destination)
        for route_name in SECTION_ONLY_ROUTES:
            self.assertNotIn(route_name, seen)

    def test_deep_routes_resolve_to_their_owning_group_and_destination(self):
        cases = {
            # The six routes that returned an empty active_section on main.
            'studio_user_detail': ('people', 'users'),
            'studio_user_import': ('people', 'users'),
            'studio_assistant': ('people', 'assistant'),
            'studio_maven_event_list': ('operations', 'maven_events'),
            'studio_questionnaire_response_queue': ('onboarding', 'questionnaires'),
            'studio_stripe_webhooks': ('people', ''),
            # Existing exact behaviour that must not regress.
            'studio_event_list': ('events', 'events'),
            'studio_event_series_detail': ('events', 'event_series'),
            'studio_host_edit': ('events', 'event_hosts'),
            'studio_article_edit': ('content', 'articles'),
            'studio_course_enrollment_list': ('content', 'courses'),
            'studio_workshop_detail': ('content', 'workshops'),
            'studio_sprint_detail': ('planning', 'sprints'),
            'studio_plan_edit': ('planning', 'plans'),
            'studio_book_detail': ('planning', 'books'),
            'studio_questionnaire_response_detail': ('onboarding', 'questionnaires'),
            'studio_persona_edit': ('onboarding', 'personas'),
            'studio_campaign_detail': ('communication', 'campaigns'),
            'studio_email_template_edit': ('communication', 'email_templates'),
            'studio_utm_campaign_detail': ('tracking', 'utm_campaigns'),
            'studio_utm_campaign_analytics': ('tracking', 'utm_analytics'),
            'studio_signup_analytics': ('tracking', 'signup_analytics'),
            'studio_trigger_delivery_list': ('operations', 'trigger_deliveries'),
            'studio_settings_save': ('operations', 'settings'),
            'studio_api_token_created': ('operations', 'api_tokens'),
            'studio_maven_event_detail': ('operations', 'maven_events'),
            'studio_dashboard': ('', 'dashboard'),
        }
        for route_name, expected in cases.items():
            with self.subTest(route=route_name):
                self.assertEqual(ROUTE_INDEX[route_name], expected)

    def test_routes_without_a_home_expand_nothing_and_mark_nothing(self):
        for route_name in ROUTES_WITHOUT_SIDEBAR_HOME:
            with self.subTest(route=route_name):
                self.assertNotIn(route_name, ROUTE_INDEX)


class SidebarStateTest(TestCase):
    """``sidebar_state`` derives expansion + current link from the route."""

    def test_active_section_expands_and_marks_one_destination(self):
        state = sidebar_state(reverse('studio_user_detail', args=[7]))
        self.assertEqual(state['active_section'], 'people')
        self.assertEqual(state['active_destination'], 'users')
        self.assertTrue(state['people_active'])
        self.assertFalse(state['events_active'])
        self.assertFalse(state['events_expanded'])

    def test_dashboard_keeps_events_expanded_without_a_current_child(self):
        state = sidebar_state(reverse('studio_dashboard'))
        self.assertEqual(state['active_section'], '')
        self.assertEqual(state['active_destination'], 'dashboard')
        self.assertTrue(state['events_expanded'])
        self.assertFalse(state['events_active'])

    def test_nested_triggers_open_only_for_trigger_destinations(self):
        state = sidebar_state(reverse('studio_trigger_emission_list'))
        self.assertTrue(state['operations_active'])
        self.assertTrue(state['triggers_active'])

        state = sidebar_state(reverse('studio_worker'))
        self.assertTrue(state['operations_active'])
        self.assertFalse(state['triggers_active'])

    def test_section_only_route_expands_its_group_without_a_destination(self):
        state = sidebar_state(reverse('studio_stripe_webhooks'))
        self.assertTrue(state['people_active'])
        self.assertEqual(state['active_destination'], '')

    def test_request_resolver_match_is_the_authority(self):
        request = RequestFactory().get('/studio/users/9/')
        request.resolver_match = type(
            'FakeMatch', (), {'url_name': 'studio_maven_event_detail'},
        )()
        state = sidebar_state(request)
        self.assertEqual(state['active_section'], 'operations')
        self.assertEqual(state['active_destination'], 'maven_events')

    def test_missing_resolver_metadata_degrades_safely(self):
        for target in (None, '', '/studio/not-a-real-route/', object()):
            with self.subTest(target=repr(target)):
                state = sidebar_state(target)
                self.assertEqual(state['active_section'], '')
                self.assertEqual(state['active_destination'], '')
                self.assertTrue(state['events_expanded'])
                self.assertFalse(state['people_active'])


class SidebarRenderedActiveStateTest(TestCase):
    """The rendered sidebar expands one group and marks one link current."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='sidebar-1435@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-1435@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def _sidebar_html(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        start = body.index('id="studio-sidebar-nav"')
        return body[start:body.index('</nav>', start)]

    def _current_hrefs(self, nav_html):
        return [
            HREF_RE.search(anchor).group(1)
            for anchor in ANCHOR_RE.findall(nav_html)
            if 'aria-current="page"' in anchor
        ]

    def _assert_expanded(self, nav_html, slug):
        self.assertIn(f'id="studio-section-{slug}" class="space-y-1 mt-1"', nav_html)

    def _assert_collapsed(self, nav_html, slug):
        self.assertIn(
            f'id="studio-section-{slug}" class="space-y-1 mt-1 hidden"', nav_html,
        )

    def _assert_active_link(self, nav_html, href):
        anchors = [
            anchor for anchor in ANCHOR_RE.findall(nav_html)
            if HREF_RE.search(anchor) and HREF_RE.search(anchor).group(1) == href
        ]
        self.assertEqual(len(anchors), 1, f'expected one sidebar link for {href}')
        self.assertIn('bg-secondary text-foreground', anchors[0])

    def _assert_single_current(self, nav_html, href, section):
        self.assertEqual(self._current_hrefs(nav_html), [href])
        self._assert_active_link(nav_html, href)
        self._assert_expanded(nav_html, section)
        # No second link (or the Triggers disclosure) may also read as active.
        self.assertEqual(nav_html.count('bg-secondary text-foreground'), 1)

    def test_user_detail_expands_people_and_marks_users_current(self):
        nav = self._sidebar_html(reverse('studio_user_detail', args=[self.member.pk]))
        self._assert_single_current(nav, '/studio/users/', 'people')
        self._assert_collapsed(nav, 'events')

    def test_contacts_import_expands_people_and_marks_users_current(self):
        nav = self._sidebar_html(reverse('studio_user_import'))
        self._assert_single_current(nav, '/studio/users/', 'people')

    def test_assistant_expands_people_and_marks_assistant_current(self):
        nav = self._sidebar_html(reverse('studio_assistant'))
        self._assert_single_current(nav, '/studio/assistant/', 'people')

    def test_maven_events_expand_operations_and_mark_maven_current(self):
        nav = self._sidebar_html(reverse('studio_maven_event_list'))
        self._assert_single_current(nav, '/studio/maven-events/', 'operations')

    def test_response_queue_expands_onboarding_and_marks_questionnaires(self):
        nav = self._sidebar_html(reverse('studio_questionnaire_response_queue'))
        self._assert_single_current(nav, '/studio/questionnaires/', 'onboarding')

    def _assert_active_link_absent(self, nav_html, href):
        anchors = [
            anchor for anchor in ANCHOR_RE.findall(nav_html)
            if HREF_RE.search(anchor) and HREF_RE.search(anchor).group(1) == href
        ]
        self.assertEqual(len(anchors), 1)
        self.assertNotIn('bg-secondary text-foreground', anchors[0])
        self.assertNotIn('aria-current', anchors[0])

    def test_stripe_webhooks_expand_people_without_a_current_link(self):
        nav = self._sidebar_html(reverse('studio_stripe_webhooks'))
        self._assert_expanded(nav, 'people')
        self.assertEqual(self._current_hrefs(nav), [])
        self.assertIn('data-studio-active-section="people"', nav)
        # The Payment mismatches neighbour must not be marked current.
        self._assert_active_link_absent(nav, '/studio/users/payment-mismatches/')

    def test_dashboard_marks_only_the_dashboard_link(self):
        nav = self._sidebar_html(reverse('studio_dashboard'))
        self.assertEqual(self._current_hrefs(nav), ['/studio/'])
        self._assert_expanded(nav, 'events')
        self._assert_active_link_absent(nav, '/studio/events/')

    def test_deep_content_and_planning_routes_keep_their_homes(self):
        cases = (
            (reverse('studio_event_list_past'), '/studio/events/', 'events'),
            (reverse('studio_host_list'), '/studio/hosts/', 'events'),
            (reverse('studio_article_list'), '/studio/articles/', 'content'),
            (reverse('studio_sprint_list'), '/studio/sprints/', 'planning'),
            (reverse('studio_notification_log'), '/studio/notifications/',
             'communication'),
            (reverse('studio_utm_campaign_list'), '/studio/utm-campaigns/',
             'tracking'),
            (reverse('studio_email_log_list'), '/studio/email-log/', 'operations'),
            (reverse('studio_settings'), '/studio/settings/', 'operations'),
        )
        for url, href, section in cases:
            with self.subTest(url=url):
                nav = self._sidebar_html(url)
                self._assert_single_current(nav, href, section)

    def test_trigger_route_opens_the_nested_disclosure_and_marks_one_child(self):
        nav = self._sidebar_html(reverse('studio_trigger_emission_list'))
        self._assert_expanded(nav, 'operations')
        self.assertEqual(self._current_hrefs(nav), ['/studio/triggers/emissions/'])
        self._assert_active_link(nav, '/studio/triggers/emissions/')
        self.assertIn(
            'id="studio-triggers-children" class="mt-1 ml-5 space-y-1 '
            'border-l border-border pl-2"',
            nav,
        )

    def test_superuser_only_api_tokens_page_marks_its_own_link(self):
        superuser = User.objects.create_user(
            email='root-1435@test.com', password='pw',
            is_staff=True, is_superuser=True,
        )
        self.client.force_login(superuser)
        nav = self._sidebar_html(reverse('studio_api_token_list'))
        self._assert_single_current(nav, '/studio/api-tokens/', 'operations')

    def test_active_section_attribute_matches_the_expanded_group(self):
        response = self.client.get(reverse('studio_assistant'))
        self.assertContains(response, 'data-studio-active-section="people"')
