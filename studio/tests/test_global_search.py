"""Focused tests for Studio global search (#1191)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Article, Course, Download, Project, Workshop
from email_app.models import EmailCampaign
from events.models import Event, EventSeries
from studio.views.global_search import GROUP_NAMES, NAVIGATION_ITEMS

User = get_user_model()


class StudioGlobalSearchTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff-global-search@test.com',
            password='pw',
            is_staff=True,
        )
        self.member = User.objects.create_user(
            email='needle-member@test.com',
            password='pw',
            first_name='Needle',
            last_name='Member',
            email_verified=True,
        )
        self.url = reverse('studio_global_search')

    def _login_staff(self):
        self.client.login(email=self.staff.email, password='pw')

    def _get_results(self, query):
        self._login_staff()
        response = self.client.get(self.url, {'q': query})
        self.assertEqual(response.status_code, 200)
        return response.json()['results']

    def test_anonymous_and_non_staff_are_blocked(self):
        anon_response = self.client.get(self.url, {'q': 'needle'})
        self.assertEqual(anon_response.status_code, 302)
        self.assertIn('/accounts/login/', anon_response['Location'])

        non_staff = User.objects.create_user(
            email='non-staff-search@test.com',
            password='pw',
        )
        self.client.login(email=non_staff.email, password='pw')
        non_staff_response = self.client.get(self.url, {'q': 'needle'})
        self.assertEqual(non_staff_response.status_code, 403)

    def test_short_queries_return_empty_groups(self):
        results = self._get_results(' n ')

        self.assertEqual(results, {group: [] for group in GROUP_NAMES})

    def test_grouped_results_include_compact_payloads_only(self):
        Article.objects.create(
            title='Needle Article',
            slug='needle-article',
            date=timezone.now().date(),
            content_markdown='private article body',
            published=True,
        )
        Course.objects.create(
            title='Needle Course',
            slug='needle-course',
            description='private course body',
            status='published',
        )
        Event.objects.create(
            title='Needle Event',
            slug='needle-event',
            status='upcoming',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            description='private event body',
        )
        EmailCampaign.objects.create(
            subject='Needle Campaign',
            body='private campaign body',
            status='draft',
        )

        results = self._get_results('needle')

        self.assertEqual(set(results), set(GROUP_NAMES))
        for group in ('users', 'articles', 'courses', 'events', 'campaigns'):
            self.assertTrue(results[group], group)
        for group, items in results.items():
            for item in items:
                self.assertEqual(item['group'], group)
                self.assertIn('label', item)
                self.assertIn('type', item)
                self.assertIn('metadata', item)
                self.assertTrue(item['url'].startswith('/studio/'))
                self.assertNotIn('body', item)
                self.assertNotIn('content_markdown', item)
                self.assertNotIn('description', item)
                self.assertNotIn('transcript_text', item)

    def test_user_search_matches_email_name_and_id_with_expected_url(self):
        by_email = self._get_results('needle-member@test.com')['users'][0]
        self.assertEqual(by_email['label'], 'Needle Member')
        self.assertEqual(
            by_email['url'],
            reverse('studio_user_detail', kwargs={'user_id': self.member.pk}),
        )

        by_name = self._get_results('Member')['users'][0]
        self.assertEqual(by_name['id'], self.member.pk)

        by_id = self._get_results(f'{self.member.pk:02d}')['users'][0]
        self.assertEqual(by_id['id'], self.member.pk)

    def test_content_events_and_campaigns_link_to_existing_studio_routes(self):
        article = Article.objects.create(
            title='Omni Article',
            slug='omni-article',
            date=timezone.now().date(),
            published=True,
        )
        Course.objects.create(
            title='Omni Course',
            slug='omni-course',
            status='published',
        )
        Workshop.objects.create(
            title='Omni Workshop',
            slug='omni-workshop',
            date=timezone.now().date(),
        )
        recording = Event.objects.create(
            title='Omni Recording',
            slug='omni-recording',
            status='completed',
            start_datetime=timezone.now() - timezone.timedelta(days=1),
            recording_url='https://example.com/video',
        )
        Download.objects.create(
            title='Omni Download',
            slug='omni-download',
            file_url='https://example.com/file.pdf',
        )
        Project.objects.create(
            title='Omni Project',
            slug='omni-project',
            date=timezone.now().date(),
            status='pending_review',
            published=False,
        )
        event = Event.objects.create(
            title='Omni Event',
            slug='omni-event',
            status='upcoming',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
        )
        campaign = EmailCampaign.objects.create(
            subject='Omni Campaign',
            body='Campaign body',
            status='draft',
        )

        results = self._get_results('omni')

        self.assertIn(
            reverse('studio_article_edit', kwargs={'article_id': article.pk}),
            {item['url'] for item in results['articles']},
        )
        self.assertIn(
            reverse('studio_recording_edit', kwargs={'recording_id': recording.pk}),
            {item['url'] for item in results['recordings']},
        )
        self.assertIn(
            reverse('studio_event_edit', kwargs={'event_id': event.pk}),
            {item['url'] for item in results['events']},
        )
        self.assertEqual(
            results['campaigns'][0]['url'],
            reverse('studio_campaign_detail', kwargs={'campaign_id': campaign.pk}),
        )

    def test_result_groups_are_capped_and_rank_exact_then_prefix_before_substring(self):
        exact = User.objects.create_user(email='ranked@example.com', password='pw')
        prefix = User.objects.create_user(email='ranked-prefix@example.com', password='pw')
        loose = User.objects.create_user(email='loose-ranked@example.com', password='pw')
        for index in range(9):
            User.objects.create_user(
                email=f'caponly-{index}@example.com',
                password='pw',
            )

        users = self._get_results('ranked')['users']

        self.assertEqual(users[0]['id'], exact.pk)
        user_ids = [item['id'] for item in users]
        self.assertLess(user_ids.index(prefix.pk), user_ids.index(loose.pk))

        capped_users = self._get_results('caponly')['users']
        self.assertEqual(len(capped_users), 8)

    def test_event_series_and_navigation_have_canonical_urls(self):
        series = EventSeries.objects.create(
            name='Needle Operator Series',
            slug='needle-operator-series',
            start_time=timezone.now().time(),
        )

        results = self._get_results('needle operator')
        self.assertEqual(
            results['event_series'][0]['url'],
            reverse(
                'studio_event_series_detail',
                kwargs={'series_id': series.pk},
            ),
        )
        settings = self._get_results('settings')['pages'][0]
        self.assertEqual(settings['label'], 'Settings')
        self.assertEqual(settings['url'], reverse('studio_settings'))

    def test_every_sidebar_destination_is_searchable_with_privilege_filtering(self):
        for label, route_name, _aliases, superuser_only in NAVIGATION_ITEMS:
            with self.subTest(label=label):
                results = self._get_results(label)['pages']
                urls = {item['url'] for item in results}
                if superuser_only:
                    self.assertNotIn(reverse(route_name), urls)
                else:
                    self.assertIn(reverse(route_name), urls)

        self.client.logout()
        self.staff.is_superuser = True
        self.staff.save(update_fields=['is_superuser'])
        for label, route_name, _aliases, superuser_only in NAVIGATION_ITEMS:
            if not superuser_only:
                continue
            with self.subTest(superuser_label=label):
                self.assertIn(
                    reverse(route_name),
                    {item['url'] for item in self._get_results(label)['pages']},
                )

    def test_host_aliases_use_clarified_labels(self):
        event_hosts = self._get_results('event hosts')['pages'][0]
        call_hosts = self._get_results('call hosts')['pages'][0]
        self.assertEqual(event_hosts['label'], 'Event hosts')
        self.assertEqual(call_hosts['label'], 'Call hosts (scheduling)')

    def test_no_token_authenticated_global_search_api_is_added(self):
        self._login_staff()
        response = self.client.get('/api/global-search/', {'q': 'needle'}, follow=True)

        self.assertEqual(response.status_code, 404)
