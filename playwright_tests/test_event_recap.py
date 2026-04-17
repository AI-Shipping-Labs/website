"""
Playwright E2E tests for Event Recap landing pages (Issue #191).

Tests cover the BDD scenarios from the issue:
- Visitor lands on the launch recap and finds the recording
- Visitor explores key topics and activities
- Visitor compares membership plans
- Visitor reviews upcoming events and clicks Register
- Existing event detail page surfaces the recap when one exists
- Event without a recap does not expose the recap URL
- Recap with only some sections renders cleanly without broken placeholders
- Early-member CTA invites a personal conversation

Usage:
    uv run pytest playwright_tests/test_event_recap.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_events():
    from events.models import Event
    Event.objects.all().delete()
    connection.close()


def _full_recap():
    return {
        'hero': {
            'eyebrow': 'Event Recap',
            'title': 'AI Shipping Labs Launch Stream Recap',
            'subtitle': 'If you missed the launch stream, this page gives you the key ideas.',
            'duration': '90-minute live session',
            'format': 'Community-focused format',
            'primary_cta': {'label': 'Join AI Shipping Labs', 'href': '#plans'},
            'secondary_cta': {
                'label': 'Read the full summary',
                'href': 'https://docs.google.com/document/d/123',
                'external': True,
            },
            'jump_to': [
                {'label': 'Recording', 'href': '#watch-stream'},
                {'label': 'Plans', 'href': '#plans'},
            ],
        },
        'watch_stream': {
            'embed_url': 'https://www.youtube.com/embed/WQAs1LNxdvM',
            'title': 'Watch the launch stream',
        },
        'key_topics': {
            'section_title': 'What You Need to Know',
            'items': [
                {'title': 'The core problem', 'summary': 'Builders need execution.'},
                {'title': 'The learning model', 'summary': 'Learn by building.'},
                {'title': 'What members do', 'summary': 'Weekly rhythm.'},
            ],
        },
        'activities': {
            'section_title': 'Main Community Activities',
            'items': [
                {'title': '1. Accountability circles', 'hook': 'Build in sprints.',
                 'details': ['Pick a project.']},
                {'title': '2. Group learning', 'hook': 'Shared leverage.',
                 'details': ['Research a tool.']},
                {'title': '3. Building sessions', 'hook': 'Live working sessions.',
                 'details': ['90-120 minute sessions.']},
                {'title': '4. Trend breakdowns', 'hook': 'Without hype.',
                 'details': ['Engineering lens.']},
                {'title': '5. Career support', 'hook': 'Real career moves.',
                 'details': ['Discuss interviews.']},
            ],
        },
        'early_member': {
            'section_title': 'Why Joining Early Matters',
            'plan_steps': [
                'Answer questions.',
                'Optional live chat.',
                'Alexey reviews.',
                'Apply in sprints.',
            ],
            'focus_areas_title': 'This plan can focus on:',
            'focus_areas': [
                'Build a clearer learning path.',
                'Start or improve a real project.',
                'Prepare for a new role.',
                'Grow in current role.',
                'Get unstuck.',
            ],
            'primary_cta': {'label': 'Join Main Tier', 'tier': 'main'},
            'secondary_cta': {'label': 'Ask about your plan',
                              'href': 'mailto:team@aishippinglabs.com'},
        },
        'upcoming_events': {
            'section_title': 'Next Live Sessions',
            'items': [
                {'title': 'Deploy Your AI Agent Project',
                 'date': 'Apr 21', 'description': 'Hands-on session.',
                 'href': 'https://luma.com/j1zzd47e'},
                {'title': 'Build Your LinkedIn',
                 'date': 'Apr 28', 'description': '30-Day challenge.',
                 'href': 'https://luma.com/3jd8wugp'},
                {'title': 'Free-Style Coding',
                 'date': 'May 19', 'description': 'Community-shaped.',
                 'href': 'https://luma.com/9gms31lk'},
                {'title': 'Take-Home Live',
                 'date': 'Jun 1', 'description': 'Live walkthrough.',
                 'href': 'https://luma.com/8s6lta91'},
            ],
        },
        'plans': {
            'section_title': 'Pick the Right Level of Support',
            'items': [
                {'tier': 'basic', 'label': 'Content only',
                 'description': 'Written summaries.', 'best_for': 'Material only.',
                 'highlight': False},
                {'tier': 'main', 'label': 'Most popular',
                 'description': 'Full community access.', 'best_for': 'Structure.',
                 'highlight': True},
                {'tier': 'premium', 'label': 'Deepest support',
                 'description': 'Community + courses.', 'best_for': 'Deeper learning.',
                 'highlight': False,
                 'extras': ['Potential course directions include Python for AI Engineering.']},
            ],
        },
        'final_cta': {
            'title': 'Ready to Join?',
            'description': 'Pick a plan.',
            'buttons': [{'label': 'View plans', 'href': '#plans'}],
        },
    }


def _create_event_with_recap(slug='launch', recap=None, status='completed'):
    from events.models import Event
    if recap is None:
        recap = _full_recap()
    event = Event.objects.create(
        title='AI Shipping Labs Community Launch',
        slug=slug,
        start_datetime=timezone.now() - datetime.timedelta(days=2),
        status=status,
        recap=recap,
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestRecapPageRendersFullContent:

    def test_visitor_finds_recording_and_jump_nav(self, django_server, page):
        _clear_events()
        _create_event_with_recap()

        page.goto(f"{django_server}/events/launch/recap",
                  wait_until="domcontentloaded")
        body = page.content()

        assert 'AI Shipping Labs Launch Stream Recap' in body
        assert 'If you missed the launch stream' in body

        # YouTube iframe present
        iframe = page.locator('iframe[src*="WQAs1LNxdvM"]')
        assert iframe.count() == 1


@pytest.mark.django_db(transaction=True)
class TestRecapKeyTopicsAndActivities:

    def test_key_topics_and_activities_render(self, django_server, page):
        _clear_events()
        _create_event_with_recap()

        page.goto(f"{django_server}/events/launch/recap",
                  wait_until="domcontentloaded")
        body = page.content()

        # 3 key topics
        assert 'The core problem' in body
        assert 'The learning model' in body
        assert 'What members do' in body
        # 5 activities
        assert '1. Accountability circles' in body
        assert '2. Group learning' in body
        assert '3. Building sessions' in body
        assert '4. Trend breakdowns' in body
        assert '5. Career support' in body


@pytest.mark.django_db(transaction=True)
class TestRecapPlansAndCheckout:

    def test_plan_cards_link_to_stripe(self, django_server, page):
        _clear_events()
        _create_event_with_recap()

        page.goto(f"{django_server}/events/launch/recap",
                  wait_until="domcontentloaded")
        body = page.content()

        # All three plan cards present
        assert 'Most popular' in body
        # Premium extras
        assert 'Python for AI Engineering' in body
        # Stripe links rendered (real settings.STRIPE_PAYMENT_LINKS map values)
        assert 'buy.stripe.com' in body
        # Each plan has data-tier attribute
        for tier in ('basic', 'main', 'premium'):
            assert f'data-tier="{tier}"' in body


@pytest.mark.django_db(transaction=True)
class TestRecapUpcomingEvents:

    def test_upcoming_events_link_externally(self, django_server, page):
        _clear_events()
        _create_event_with_recap()

        page.goto(f"{django_server}/events/launch/recap",
                  wait_until="domcontentloaded")

        register_links = page.locator('a:has-text("Register on Luma")')
        assert register_links.count() == 4
        first_href = register_links.first.get_attribute('href')
        assert first_href and 'luma.com' in first_href


@pytest.mark.django_db(transaction=True)
class TestEventDetailSurfacesRecapLink:

    def test_event_detail_links_to_recap(self, django_server, page):
        _clear_events()
        _create_event_with_recap(status='upcoming')

        page.goto(f"{django_server}/events/launch", wait_until="domcontentloaded")
        body = page.content()
        assert 'View event recap' in body
        assert '/events/launch/recap' in body


@pytest.mark.django_db(transaction=True)
class TestEventWithoutRecap:

    def test_no_recap_link_and_404(self, django_server, page):
        _clear_events()
        from events.models import Event
        Event.objects.create(
            title='No Recap Event', slug='test-no-recap',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        connection.close()

        page.goto(f"{django_server}/events/test-no-recap",
                  wait_until="domcontentloaded")
        body = page.content()
        assert 'View event recap' not in body

        # Recap URL returns 404
        response = page.goto(f"{django_server}/events/test-no-recap/recap",
                             wait_until="domcontentloaded")
        assert response.status == 404


@pytest.mark.django_db(transaction=True)
class TestRecapPartialSections:

    def test_partial_recap_renders_only_present_sections(self, django_server, page):
        _clear_events()
        _create_event_with_recap(
            slug='partial',
            recap={
                'hero': {'title': 'Partial Recap', 'subtitle': 'Just hero + plans'},
                'plans': {
                    'items': [
                        {'tier': 'main', 'label': 'Main', 'description': 'X',
                         'best_for': 'Y', 'highlight': True},
                    ],
                },
            },
        )

        page.goto(f"{django_server}/events/partial/recap",
                  wait_until="domcontentloaded")
        body = page.content()
        assert 'Partial Recap' in body
        # Plans section rendered
        assert 'id="plans"' in body
        # Omitted sections are absent
        assert 'id="watch-stream"' not in body
        assert 'What You Need to Know' not in body
        assert 'Main Community Activities' not in body
        assert 'Why Joining Early Matters' not in body
        assert 'id="upcoming-events"' not in body


@pytest.mark.django_db(transaction=True)
class TestEarlyMemberCTA:

    def test_early_member_cta_renders(self, django_server, page):
        _clear_events()
        _create_event_with_recap()

        page.goto(f"{django_server}/events/launch/recap",
                  wait_until="domcontentloaded")
        body = page.content()
        assert 'Why Joining Early Matters' in body
        # All five focus areas
        for focus in (
            'Build a clearer learning path.',
            'Start or improve a real project.',
            'Prepare for a new role.',
            'Grow in current role.',
            'Get unstuck.',
        ):
            assert focus in body
        # Mailto CTA
        assert 'mailto:team@aishippinglabs.com' in body
