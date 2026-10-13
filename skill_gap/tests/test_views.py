"""View tests for the stateless ``/skill-gap`` page (issue #1716)."""

import datetime

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext

from content.models import Course, Project, Workshop
from skill_gap.backgrounds import all_backgrounds

URL = '/skill-gap'
FIELD_GUIDE_KEYS = (
    'data-engineer',
    'data-scientist',
    'ml-engineer',
    'backend-engineer',
    'frontend-engineer',
)


def _every_background_line():
    """Strengths and gaps of every background (the content that must not leak)."""
    lines = []
    for profile in all_backgrounds().values():
        lines.extend(profile['already_have'])
        lines.extend(profile['need_to_learn'])
    return lines


@tag('core')
class AnonymousGateTest(TestCase):
    """Anonymous visitors get the sign-in card and no background content."""

    def test_anonymous_gets_sign_in_card_without_content(self):
        for url in (URL, f'{URL}?background=data-engineer'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertTemplateUsed(response, 'content/_gated_access_card.html')
                self.assertContains(response, 'data-testid="skill-gap-gated-card"')
                self.assertContains(response, 'Sign in to see your skill-gap analysis')
                self.assertContains(response, 'data-testid="skill-gap-teaser"')
                self.assertNotContains(response, 'data-testid="skill-gap-picker"')
                self.assertNotContains(response, 'data-testid="skill-gap-strengths"')
                for line in _every_background_line():
                    self.assertNotContains(response, line)
                for profile in all_backgrounds().values():
                    self.assertNotContains(response, profile['hook'])


    def test_sign_in_returns_to_the_shared_background(self):
        response = self.client.get(URL, {'background': 'data-engineer'})
        html = response.content.decode()

        card = html[html.index('data-testid="skill-gap-gated-card"'):]
        self.assertIn(
            'href="/accounts/login/?next=%2Fskill-gap%3Fbackground%3Ddata-engineer"',
            card,
        )

    def test_unknown_background_is_dropped_from_the_return_url(self):
        response = self.client.get(URL, {'background': '//evil.example'})

        html = response.content.decode()
        card = html[html.index('data-testid="skill-gap-gated-card"'):]
        self.assertIn('href="/accounts/login/?next=%2Fskill-gap"', card)
        self.assertNotIn('evil.example', card)


@tag('core')
class SignedInPickerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='free-skill-gap@example.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_free_member_sees_the_five_field_guide_backgrounds(self):
        response = self.client.get(URL)

        self.assertNotContains(response, 'data-testid="skill-gap-gated-card"')
        self.assertContains(response, 'data-testid="skill-gap-picker"')
        self.assertContains(response, 'data-testid="skill-gap-option"', count=5)
        options = response.context['options']
        self.assertEqual([option['key'] for option in options], list(FIELD_GUIDE_KEYS))
        for option in options:
            self.assertContains(
                response,
                f'data-testid="skill-gap-option-label">{option["label"]}</span>',
            )
            self.assertContains(
                response,
                f'data-testid="skill-gap-option-hook">{option["hook"]}</span>',
            )
        self.assertNotContains(response, 'data-testid="skill-gap-error"')

    def test_picker_submits_as_a_shareable_get(self):
        response = self.client.get(URL)

        self.assertContains(response, 'method="get" action="/skill-gap"')
        self.assertContains(response, 'name="background" value="data-engineer"')

    def test_each_background_renders_its_own_sections(self):
        for key, profile in all_backgrounds().items():
            with self.subTest(background=key):
                response = self.client.get(URL, {'background': key})
                analysis = response.context['analysis']
                self.assertEqual(analysis['key'], key)
                self.assertContains(
                    response, f'From {profile["label"]} to AI engineer',
                )
                self.assertNotContains(response, 'data-testid="skill-gap-picker"')
                self.assertContains(
                    response, 'data-testid="skill-gap-strength"',
                    count=len(profile['already_have']),
                )
                self.assertContains(
                    response, 'data-testid="skill-gap-gap"',
                    count=len(profile['need_to_learn']),
                )
                self.assertContains(
                    response, 'data-testid="skill-gap-path-step"',
                    count=len(profile['suggested_order']),
                )
                self.assertContains(response, profile['timeline'])
                self.assertContains(
                    response, 'data-testid="skill-gap-next-step-link"', count=3,
                )
                self.assertContains(
                    response, 'data-testid="skill-gap-next-step-type"', count=3,
                )
                for action in profile['next_actions']:
                    self.assertContains(response, f'href="{action["url"]}"')
                    self.assertContains(response, action['why'])

    def test_data_engineer_sections_render_in_order(self):
        response = self.client.get(URL, {'background': 'data-engineer'})
        html = response.content.decode()
        profile = all_backgrounds()['data-engineer']

        section_positions = [
            html.index(f'data-testid="skill-gap-{name}"')
            for name in ('strengths', 'gaps', 'path', 'timeline', 'next-steps', 'footer')
        ]
        self.assertEqual(section_positions, sorted(section_positions))
        gap_positions = [html.index(gap) for gap in profile['need_to_learn']]
        self.assertEqual(gap_positions, sorted(gap_positions))
        next_steps = html[html.index('data-testid="skill-gap-next-steps"'):]
        step_positions = [
            next_steps.index(f'href="{action["url"]}" ') for action in profile['next_actions']
        ]
        self.assertEqual(step_positions, sorted(step_positions))
        self.assertContains(response, 'Your next 3 steps')

    def test_footer_links_to_learning_paths_series_index_and_sprints(self):
        response = self.client.get(URL, {'background': 'ml-engineer'})

        self.assertContains(
            response,
            'href="https://github.com/alexeygrigorev/ai-engineering-field-guide/tree/main/learning-paths"',
        )
        self.assertContains(response, 'href="/blog/ai-engineering-field-guide"')
        self.assertContains(response, 'href="/sprints"')
        self.assertContains(response, 'data-testid="skill-gap-change-background"')

    def test_unknown_background_re_renders_picker_with_error(self):
        response = self.client.get(URL, {'background': 'astronaut'})

        self.assertIsNone(response.context['analysis'])
        self.assertContains(response, 'data-testid="skill-gap-picker"')
        self.assertContains(response, 'data-testid="skill-gap-option"', count=5)
        self.assertContains(
            response, "We don&#x27;t have a learning path for that background.",
        )
        self.assertContains(response, 'role="alert" data-testid="skill-gap-error"')
        self.assertNotContains(response, 'data-testid="skill-gap-strengths"')

    def test_empty_background_shows_picker_without_error(self):
        response = self.client.get(URL, {'background': ''})

        self.assertContains(response, 'data-testid="skill-gap-picker"')
        self.assertNotContains(response, 'data-testid="skill-gap-error"')

    def test_rendering_an_analysis_writes_nothing_to_the_database(self):
        with CaptureQueriesContext(connection) as queries:
            self.client.get(URL, {'background': 'backend-engineer'})
            self.client.get(URL, {'background': 'nope'})

        writes = [
            query['sql'] for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        self.assertEqual(writes, [])


@tag('core')
class NextStepAccessBadgeTest(TestCase):
    """Every linked course/workshop/project card shows its access level."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='free-badges@example.com', password='pw',
        )
        Course.objects.create(
            title='Python for AI Engineering', slug='python',
            status='published', required_level=30,
        )
        Workshop.objects.create(
            title='End-to-End Agent Deployment', slug='end-to-end-agent-deployment',
            status='published', date=datetime.date(2026, 4, 21),
            landing_required_level=0, pages_required_level=10,
        )
        Project.objects.create(
            title='User Satisfaction Analyst Agent',
            slug='user-satisfaction-analyst-agent', published=True,
            date=datetime.date(2026, 1, 1), required_level=0,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _card(self, html, url):
        html = html[html.index('data-testid="skill-gap-next-steps"'):]
        start = html.index(f'href="{url}"')
        return html[start:html.index('</article>', start)]

    def test_premium_course_step_shows_lock_badge_to_a_free_member(self):
        response = self.client.get(URL, {'background': 'data-scientist'})
        html = response.content.decode()

        course_card = self._card(html, '/courses/python')
        self.assertIn(
            'data-testid="skill-gap-next-step-access" data-required-level="30"',
            course_card,
        )
        self.assertIn('Premium', course_card)
        self.assertLess(
            course_card.index('skill-gap-next-step-access'),
            course_card.index('skill-gap-next-step-title'),
        )
        workshop_card = self._card(html, '/workshops/end-to-end-agent-deployment')
        self.assertIn(
            'data-testid="skill-gap-next-step-access" data-required-level="10"',
            workshop_card,
        )
        project_card = self._card(html, '/projects/user-satisfaction-analyst-agent')
        self.assertIn(
            'data-testid="skill-gap-next-step-access" data-required-level="0"',
            project_card,
        )

    def test_sprint_and_missing_assets_render_no_access_badge(self):
        # Frontend: /courses/python exists; the workshop is not in the DB
        # and the /sprints program index has no single level.
        response = self.client.get(URL, {'background': 'frontend-engineer'})
        html = response.content.decode()

        self.assertContains(response, 'data-testid="skill-gap-next-step-access"', count=1)
        self.assertNotIn('skill-gap-next-step-access', self._card(html, '/sprints'))
        self.assertNotIn(
            'skill-gap-next-step-access',
            self._card(html, '/workshops/full-stack-vibe-coding'),
        )


class SitemapExclusionTest(TestCase):
    def test_skill_gap_is_not_in_the_sitemap(self):
        response = self.client.get('/sitemap.xml')

        self.assertContains(response, '<urlset')
        self.assertNotContains(response, '/skill-gap')
