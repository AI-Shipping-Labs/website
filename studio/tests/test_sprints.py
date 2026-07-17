"""Studio admin views for sprints (issue #432)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from plans.models import Sprint

User = get_user_model()


class SprintAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def test_sprint_list_requires_staff(self):
        # Anonymous: redirect to login.
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

        # Non-staff authenticated: 403.
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        # Staff: 200.
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 200)


class SprintListRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.s1 = Sprint.objects.create(
            name='Alpha sprint', slug='alpha',
            start_date=datetime.date(2026, 4, 1),
        )
        cls.s2 = Sprint.objects.create(
            name='Beta sprint', slug='beta',
            start_date=datetime.date(2026, 6, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_list_renders_sprint_names(self):
        response = self.client.get('/studio/sprints/')
        self.assertEqual(response.status_code, 200)
        # Both sprint names appear inside the list-row link, not just
        # somewhere on the page. The template wraps each name in an
        # ``<a>`` whose href is the detail URL.
        self.assertContains(
            response,
            f'<a href="/studio/sprints/{self.s1.pk}/" class="text-accent hover:underline">Alpha sprint</a>',
            html=True,
        )
        self.assertContains(
            response,
            f'<a href="/studio/sprints/{self.s2.pk}/" class="text-accent hover:underline">Beta sprint</a>',
            html=True,
        )

    def test_sprint_list_separates_lifecycle_from_admin_status(self):
        ended_active = Sprint.objects.create(
            name='Ended but active',
            slug='ended-active',
            start_date=timezone.localdate() - datetime.timedelta(weeks=8),
            duration_weeks=6,
            status='active',
        )
        future_draft = Sprint.objects.create(
            name='Future draft',
            slug='future-draft',
            start_date=timezone.localdate() + datetime.timedelta(days=30),
            duration_weeks=6,
            status='draft',
        )

        response = self.client.get('/studio/sprints/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lifecycle')
        self.assertContains(response, 'Admin status')
        body = response.content.decode()
        ended_row = body[
            body.index(f'/studio/sprints/{ended_active.pk}/'):
            body.index('</tr>', body.index(f'/studio/sprints/{ended_active.pk}/'))
        ]
        self.assertIn('Ended', ended_row)
        self.assertIn('Active', ended_row)
        self.assertIn('data-testid="sprint-list-lifecycle"', ended_row)
        self.assertIn('data-testid="sprint-list-admin-status"', ended_row)
        future_row = body[
            body.index(f'/studio/sprints/{future_draft.pk}/'):
            body.index('</tr>', body.index(f'/studio/sprints/{future_draft.pk}/'))
        ]
        self.assertIn('Upcoming', future_row)
        self.assertIn('Draft', future_row)


class SprintCreateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_create_post_creates_row_and_redirects(self):
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Summer 2026',
            'slug': 'summer-2026',
            'start_date': '2026-07-01',
            'duration_weeks': '6',
            'status': 'draft',
            'description': 'A focused building cohort.',
            'outcomes': 'Prototype\nLaunch',
            'audience': 'AI builders',
        })
        self.assertEqual(Sprint.objects.count(), before + 1)
        sprint = Sprint.objects.get(slug='summer-2026')
        self.assertEqual(sprint.name, 'Summer 2026')
        self.assertEqual(sprint.duration_weeks, 6)
        self.assertEqual(sprint.start_date, datetime.date(2026, 7, 1))
        self.assertEqual(sprint.description, 'A focused building cohort.')
        self.assertEqual(sprint.outcomes, 'Prototype\nLaunch')
        self.assertEqual(sprint.audience, 'AI builders')
        self.assertRedirects(response, f'/studio/sprints/{sprint.pk}/')

    def test_sprint_create_rejects_duplicate_slug(self):
        Sprint.objects.create(
            name='Existing', slug='dup',
            start_date=datetime.date(2026, 5, 1),
        )
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Another',
            'slug': 'dup',
            'start_date': '2026-06-01',
            'duration_weeks': '6',
            'status': 'draft',
        })
        # Form re-renders with an error, no new row.
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'already exists', status_code=400)
        self.assertEqual(Sprint.objects.count(), before)

    def test_sprint_create_form_defaults_to_main_min_tier(self):
        response = self.client.get('/studio/sprints/new')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form_data']['min_tier_level'], '20')
        self.assertContains(response, '<option value="20" selected>Main and above</option>', html=True)
        self.assertContains(response, 'Default Main.')
        self.assertNotContains(response, 'Default Premium.')

    def test_sprint_create_form_selects_use_studio_select_class(self):
        response = self.client.get('/studio/sprints/new')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'select.studio-select')
        self.assertContains(response, 'appearance: none')
        self.assertContains(response, '-webkit-appearance: none')
        self.assertContains(response, '-moz-appearance: none')
        self.assertContains(response, 'hsl(var(--muted-foreground))')
        content = response.content.decode()
        status_pos = content.index('name="status"')
        status_tag = content[content.rfind('<select', 0, status_pos):status_pos + 250]
        min_tier_pos = content.index('name="min_tier_level"')
        min_tier_tag = content[content.rfind('<select', 0, min_tier_pos):min_tier_pos + 300]
        self.assertIn('studio-select', status_tag)
        self.assertIn('studio-select', min_tier_tag)


class SprintEditTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='To edit', slug='to-edit',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status='draft',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_sprint_edit_updates_status(self):
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/edit', {
            'name': self.sprint.name,
            'slug': self.sprint.slug,
            'start_date': self.sprint.start_date.isoformat(),
            'duration_weeks': str(self.sprint.duration_weeks),
            'status': 'active',
            'description': 'Updated public description.',
            'outcomes': 'One\nTwo',
            'audience': 'Builders\nFounders',
        })
        self.assertEqual(response.status_code, 302)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'active')
        self.assertEqual(self.sprint.description, 'Updated public description.')
        self.assertEqual(self.sprint.outcomes, 'One\nTwo')
        self.assertEqual(self.sprint.audience, 'Builders\nFounders')

        form = self.client.get(f'/studio/sprints/{self.sprint.pk}/edit')
        self.assertContains(form, 'Updated public description.')
        self.assertContains(form, 'One\nTwo')
        self.assertContains(form, 'Builders\nFounders')
        self.assertContains(form, 'Enter one item per line.')

    def test_blank_landing_fields_are_accepted(self):
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/edit', {
            'name': self.sprint.name,
            'slug': self.sprint.slug,
            'start_date': self.sprint.start_date.isoformat(),
            'duration_weeks': str(self.sprint.duration_weeks),
            'status': self.sprint.status,
            'description': '',
            'outcomes': '',
            'audience': '',
        })
        self.assertEqual(response.status_code, 302)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.description, '')
        self.assertEqual(self.sprint.outcomes, '')
        self.assertEqual(self.sprint.audience, '')


class SprintDetailEditAccessTest(TestCase):
    """Detail / edit / new pages: 200 for staff, 403/redirect otherwise."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )

    def test_staff_can_reach_all_sprint_pages(self):
        self.client.login(email='staff@test.com', password='pw')
        for url in [
            '/studio/sprints/',
            '/studio/sprints/new',
            f'/studio/sprints/{self.sprint.pk}/',
            f'/studio/sprints/{self.sprint.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, msg=f'{url} -> {response.status_code}')

    def test_non_staff_cannot_reach_sprint_pages(self):
        self.client.login(email='member@test.com', password='pw')
        for url in [
            '/studio/sprints/',
            '/studio/sprints/new',
            f'/studio/sprints/{self.sprint.pk}/',
            f'/studio/sprints/{self.sprint.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, msg=f'{url} -> {response.status_code}')

    def test_anonymous_redirected_to_login(self):
        for url in [
            '/studio/sprints/',
            '/studio/sprints/new',
            f'/studio/sprints/{self.sprint.pk}/',
            f'/studio/sprints/{self.sprint.pk}/edit',
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, msg=f'{url} -> {response.status_code}')
            self.assertIn('/accounts/login/', response['Location'])

    def test_studio_sprint_detail_access_does_not_expose_public_link(self):
        url = f'/studio/sprints/{self.sprint.pk}/'

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertNotContains(response, 'data-testid="view-on-site"', status_code=302)

        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'data-testid="view-on-site"', status_code=403)


class SprintDetailLifecycleStatusTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-detail-status@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-detail-status@test.com', password='pw')

    def test_detail_separates_ended_lifecycle_from_active_admin_status(self):
        sprint = Sprint.objects.create(
            name='Ended admin active',
            slug='ended-admin-active',
            start_date=timezone.localdate() - datetime.timedelta(weeks=8),
            duration_weeks=6,
            status='active',
        )

        response = self.client.get(f'/studio/sprints/{sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lifecycle')
        self.assertContains(response, 'Admin status')
        self.assertContains(response, 'data-testid="sprint-lifecycle-badge"')
        self.assertContains(response, 'data-testid="sprint-admin-status-badge"')
        body = response.content.decode()
        lifecycle_pos = body.index('data-testid="sprint-lifecycle-badge"')
        lifecycle_block = body[lifecycle_pos:body.index('</dd>', lifecycle_pos)]
        self.assertIn('Ended', lifecycle_block)
        admin_pos = body.index('data-testid="sprint-admin-status-badge"')
        admin_block = body[admin_pos:body.index('</dd>', admin_pos)]
        self.assertIn('Active', admin_block)

    def test_complete_action_is_near_status_metadata_not_danger_zone(self):
        sprint = Sprint.objects.create(
            name='Can complete',
            slug='can-complete',
            start_date=timezone.localdate() - datetime.timedelta(weeks=8),
            duration_weeks=6,
            status='active',
        )

        response = self.client.get(f'/studio/sprints/{sprint.pk}/')

        body = response.content.decode()
        complete_pos = body.index('data-testid="sprint-complete-form"')
        danger_pos = body.index('data-testid="sprint-danger-zone"')
        self.assertLess(complete_pos, danger_pos)
        self.assertContains(response, 'Mark completed')


class SprintDetailViewOnSiteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-view-on-site@test.com',
            password='pw',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-view-on-site@test.com', password='pw')

    def _header_actions(self, response):
        body = response.content.decode()
        row_marker = 'data-testid="studio-header-actions"'
        self.assertIn(row_marker, body)
        row_start = body.index(row_marker)
        row_open = body.rfind('<header', 0, row_start)
        row_end = body.index('</header>', row_start) + len('</header>')
        return body[row_open:row_end]

    def test_header_renders_one_view_on_site_link_with_public_sprint_url(self):
        sprint = Sprint.objects.create(
            name='June 2026',
            slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
            status='active',
        )

        response = self.client.get(f'/studio/sprints/{sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="view-on-site"', count=1)
        self.assertContains(response, 'View on site', count=1)
        self.assertNotContains(response, 'data-testid="panel-view-on-site"')
        self.assertNotContains(response, 'data-testid="sticky-view-on-site"')

        header = self._header_actions(response)
        self.assertEqual(header.count('data-testid="view-on-site"'), 1)
        self.assertIn('href="/sprints/june-2026"', header)
        self.assertIn('target="_blank"', header)
        self.assertIn('rel="noopener noreferrer"', header)
        self.assertIn('View on site', header)
        self.assertIn('Open in Django admin', header)
        self.assertIn('Enroll members', header)
        self.assertIn('Edit sprint', header)
        self.assertIn('border border-border', header)

        testid_start = header.index('data-testid="view-on-site"')
        link_start = header.rfind('<a ', 0, testid_start)
        link_end = header.index('</a>', testid_start) + len('</a>')
        view_on_site_link = header[link_start:link_end]
        self.assertIn('href="/sprints/june-2026"', view_on_site_link)
        self.assertIn('min-h-[44px]', view_on_site_link)
        self.assertNotIn('/studio/sprints/', view_on_site_link)
        self.assertNotIn('/board', view_on_site_link)
        self.assertNotIn('/api/', view_on_site_link)

    def test_header_link_renders_for_all_sprint_statuses(self):
        for status in ('draft', 'active', 'completed', 'cancelled'):
            with self.subTest(status=status):
                sprint = Sprint.objects.create(
                    name=f'{status.title()} sprint',
                    slug=f'{status}-sprint',
                    start_date=datetime.date(2026, 6, 1),
                    status=status,
                )

                response = self.client.get(f'/studio/sprints/{sprint.pk}/')

                self.assertEqual(response.status_code, 200)
                header = self._header_actions(response)
                self.assertEqual(header.count('data-testid="view-on-site"'), 1)
                self.assertIn(f'href="/sprints/{status}-sprint"', header)
