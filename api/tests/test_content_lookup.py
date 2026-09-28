"""Staff content lookup by content_id UUID (issue #1834)."""

import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import MemberAPIKey, Token
from content.models import (
    Article,
    Course,
    Download,
    MarketingPage,
    Module,
    Project,
    Tutorial,
    Unit,
    Workshop,
    WorkshopPage,
)
from events.models import Event
from integrations.config import site_base_url

User = get_user_model()

COURSE_REPO = 'AI-Shipping-Labs/ai-buildcamp-course'
UNIT_COMMIT = 'abc1234567890abcdef1234567890abcdef12345'
BODY_KEYS = ('markdown', 'html', 'homework_markdown', 'homework_html')


class ContentLookupApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email='lookup-staff@test.com', is_staff=True)
        cls.member = User.objects.create_user(email='lookup-member@test.com')
        cls.token = Token.objects.create(user=cls.staff, name='lookup')
        cls.member_token = Token(key='lookup-nonstaff-token', user=cls.member, name='m')
        Token.objects.bulk_create([cls.member_token])

        today = date(2026, 9, 24)
        cls.article = Article.objects.create(
            title='Draft article', slug='draft-article', date=today,
            content_markdown='Hello **article**', published=False,
            content_id=uuid.uuid4(), source_repo='AI-Shipping-Labs/content',
            source_path='blog/draft-article.md', source_commit='f' * 40,
        )
        cls.project = Project.objects.create(
            title='Project', slug='project', date=today,
            content_markdown='Project body', content_id=uuid.uuid4(),
            source_repo='AI-Shipping-Labs/content', source_path='projects/p.md',
            source_commit='',
        )
        cls.tutorial = Tutorial.objects.create(
            title='Tutorial', slug='tutorial', date=today,
            content_markdown='Tutorial body', content_html='<p>Tutorial body</p>',
            content_id=uuid.uuid4(),
        )
        cls.download = Download.objects.create(
            title='Download', slug='download', description='Download text',
            file_url='https://example.com/file.pdf', content_id=uuid.uuid4(),
        )
        cls.workshop = Workshop.objects.create(
            title='Workshop', slug='workshop', date=today, status='published',
            description='Workshop text', content_id=uuid.uuid4(),
        )
        cls.workshop_page = WorkshopPage.objects.create(
            workshop=cls.workshop, title='Workshop page', slug='workshop-page',
            body='Page text', content_id=uuid.uuid4(),
        )
        cls.marketing_page = MarketingPage.objects.create(
            title='Marketing', public_path='/lookup-marketing', status='published',
            content_markdown='Marketing text', content_id=uuid.uuid4(),
        )
        cls.event = Event.objects.create(
            title='Event', slug='lookup-event', start_datetime=timezone.now(),
            status='completed', published=True, description='Event text',
            content_id=uuid.uuid4(),
        )

        cls.course_id = uuid.uuid4()
        cls.module_id = uuid.uuid4()
        cls.child_module_id = uuid.uuid4()
        cls.unit_id = uuid.uuid4()
        cls.child_unit_id = uuid.uuid4()
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp', status='draft',
            description='Course text', source_content_id=cls.course_id,
            source_repo=COURSE_REPO, source_path='course.yaml',
            source_commit_sha='1' * 40,
        )
        cls.module = Module.objects.create(
            title='Week 1', slug='week-1', course=cls.course,
            overview='Module overview', source_content_id=cls.module_id,
            source_path='week-1', source_commit_sha='2' * 40,
        )
        cls.child_module = Module.objects.create(
            title='Part A', slug='part-a', course=cls.course, parent=cls.module,
            source_content_id=cls.child_module_id,
        )
        cls.unit = Unit.objects.create(
            title='Setup', slug='setup', module=cls.module,
            body='## Setup\n\nNew paragraph', homework='Push your repo',
            required_level=20, source_content_id=cls.unit_id,
            source_path='week-1/01-setup.md', source_commit_sha=UNIT_COMMIT,
        )
        cls.child_unit = Unit.objects.create(
            title='Nested', slug='nested', module=cls.child_module,
            body='Nested body', source_content_id=cls.child_unit_id,
        )

    def auth(self, token=None):
        return {'HTTP_AUTHORIZATION': f'Token {(token or self.token).key}'}

    def get(self, content_id, query='', **headers):
        headers = headers or self.auth()
        return self.client.get(f'/api/content/{content_id}{query}', **headers)

    def test_every_type_resolves_with_mapped_body_fields(self):
        base = site_base_url().rstrip('/')
        cases = [
            (self.article, self.article.content_id, 'article', 'content_markdown', 'content_html'),
            (self.project, self.project.content_id, 'project', 'content_markdown', 'content_html'),
            (self.tutorial, self.tutorial.content_id, 'tutorial', 'content_markdown', 'content_html'),
            (self.download, self.download.content_id, 'download', 'description', None),
            (self.workshop, self.workshop.content_id, 'workshop', 'description', 'description_html'),
            (self.workshop_page, self.workshop_page.content_id, 'workshop_page', 'body', 'body_html'),
            (self.marketing_page, self.marketing_page.content_id, 'marketing_page', 'content_markdown', 'content_html'),
            (self.event, self.event.content_id, 'event', 'description', 'description_html'),
            (self.course, self.course_id, 'course', 'description', 'description_html'),
            (self.module, self.module_id, 'course_module', 'overview', 'overview_html'),
            (self.unit, self.unit_id, 'course_unit', 'body', 'body_html'),
        ]
        for target, content_id, type_name, md_field, html_field in cases:
            with self.subTest(type=type_name):
                target.refresh_from_db()
                data = self.get(content_id).json()
                self.assertEqual(data['content_id'], str(content_id))
                self.assertEqual(data['type'], type_name)
                self.assertEqual(data['id'], target.pk)
                self.assertEqual(data['title'], target.title)
                self.assertEqual(data['url'], base + target.get_absolute_url())
                self.assertEqual(data['share_url'], f'{base}/c/{content_id}')
                self.assertEqual(data['markdown'], getattr(target, md_field))
                self.assertTrue(data['markdown'])
                if html_field is None:
                    self.assertIsNone(data['html'])
                else:
                    self.assertEqual(data['html'], getattr(target, html_field))
                    self.assertTrue(data['html'])
                if type_name == 'course_unit':
                    self.assertEqual(data['homework_markdown'], 'Push your repo')
                    self.assertIn('Push your repo', data['homework_html'])
                else:
                    self.assertNotIn('homework_markdown', data)
                    self.assertNotIn('homework_html', data)

    def test_draft_course_gated_unit_returned_in_full(self):
        data = self.get(self.unit_id).json()
        self.assertIn('New paragraph', data['markdown'])
        self.assertIn('<p>New paragraph</p>', data['html'])
        self.assertEqual(data['required_level'], 20)
        self.assertFalse(data['is_public'])
        self.assertEqual(
            data['context'],
            {'course_slug': 'buildcamp', 'module_slug': 'week-1'},
        )
        self.assertIsNone(data['updated_at'])
        self.assertEqual(
            data['source'],
            {
                'repo': COURSE_REPO,
                'path': 'week-1/01-setup.md',
                'commit': UNIT_COMMIT,
                'github_url': (
                    f'https://github.com/{COURSE_REPO}/blob/{UNIT_COMMIT}/'
                    'week-1/01-setup.md'
                ),
            },
        )
        # Share link behavior is unchanged for the draft unit.
        self.assertEqual(self.client.get(f'/c/{self.unit_id}').status_code, 404)

    def test_is_public_follows_share_link_rule(self):
        self.assertFalse(self.get(self.article.content_id).json()['is_public'])
        self.assertTrue(self.get(self.workshop.content_id).json()['is_public'])
        self.assertEqual(
            self.client.get(f'/c/{self.workshop.content_id}').status_code, 302,
        )

    def test_context_for_nested_units_modules_and_workshop_pages(self):
        self.assertEqual(
            self.get(self.child_unit_id).json()['context'],
            {
                'course_slug': 'buildcamp',
                'module_slug': 'part-a',
                'parent_module_slug': 'week-1',
            },
        )
        self.assertEqual(
            self.get(self.child_module_id).json()['context'],
            {'course_slug': 'buildcamp', 'parent_module_slug': 'week-1'},
        )
        self.assertEqual(
            self.get(self.workshop_page.content_id).json()['context'],
            {'workshop_slug': 'workshop'},
        )
        self.assertEqual(self.get(self.article.content_id).json()['context'], {})

    def test_source_normalization(self):
        article_source = self.get(self.article.content_id).json()['source']
        self.assertEqual(article_source['repo'], 'AI-Shipping-Labs/content')
        self.assertEqual(article_source['commit'], 'f' * 40)
        self.assertTrue(article_source['github_url'].startswith(
            'https://github.com/AI-Shipping-Labs/content/blob/',
        ))

        # Empty commit becomes null and suppresses github_url.
        project_source = self.get(self.project.content_id).json()['source']
        self.assertIsNone(project_source['commit'])
        self.assertIsNone(project_source['github_url'])
        self.assertEqual(project_source['path'], 'projects/p.md')

        self.assertEqual(
            self.get(self.tutorial.content_id).json()['source'],
            {'repo': None, 'path': None, 'commit': None, 'github_url': None},
        )

        course_source = self.get(self.course_id).json()['source']
        self.assertEqual(course_source['repo'], COURSE_REPO)
        self.assertEqual(course_source['commit'], '1' * 40)

    def test_unknown_uuid_returns_404(self):
        response = self.get(uuid.uuid4())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'content_not_found')

    def test_ambiguous_uuid_returns_409_with_matches(self):
        shared = uuid.uuid4()
        article = Article.objects.create(
            title='Dup article', slug='dup-article', date=date(2026, 1, 1),
            content_id=shared,
        )
        page = WorkshopPage.objects.create(
            workshop=self.workshop, title='Dup page', slug='dup-page',
            content_id=shared,
        )
        response = self.get(shared)
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertEqual(data['code'], 'content_id_ambiguous')
        self.assertEqual(
            [(m['type'], m['id']) for m in data['matches']],
            [('article', article.pk), ('workshop_page', page.pk)],
        )

    def test_include_body_false_returns_lengths_only(self):
        for value in ('false', '0'):
            with self.subTest(value=value):
                data = self.get(self.unit_id, f'?include_body={value}').json()
                for key in BODY_KEYS:
                    self.assertNotIn(key, data)
                self.unit.refresh_from_db()
                self.assertEqual(data['markdown_length'], len(self.unit.body))
                self.assertEqual(data['html_length'], len(self.unit.body_html))
                self.assertEqual(data['source']['commit'], UNIT_COMMIT)

    def test_include_body_invalid_returns_422(self):
        response = self.get(self.unit_id, '?include_body=maybe')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'validation_error')

    def test_unauthorized_callers_get_401(self):
        _key, member_plaintext = MemberAPIKey.create_for_user(
            user=self.staff, name='staff member key',
        )
        for headers in (
            {},
            self.auth(self.member_token),
            {'HTTP_AUTHORIZATION': f'Token {member_plaintext}'},
        ):
            response = self.client.get(f'/api/content/{self.unit_id}', **headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('New paragraph', response.content.decode())

    def test_write_methods_return_405_without_changes(self):
        url = f'/api/content/{self.article.content_id}'
        for method in ('post', 'patch', 'put', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    url, data='{"title": "Changed"}',
                    content_type='application/json', **self.auth(),
                )
                self.assertEqual(response.status_code, 405)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, 'Draft article')
