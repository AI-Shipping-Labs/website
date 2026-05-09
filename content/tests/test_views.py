from datetime import date

from django.test import Client, TestCase

from content.models import Article, CuratedLink, Project, Tutorial
from events.models import Event


class HomeViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.article = Article.objects.create(
            title='Test Article',
            slug='test-article',
            description='Description',
            date=date(2025, 6, 15),
            published=True,
        )
        from django.utils import timezone
        self.recording = Event.objects.create(
            title='Test Recording',
            slug='test-recording',
            description='Workshop desc',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            published=True,
        )
        self.project = Project.objects.create(
            title='Test Project',
            slug='test-project',
            description='Project desc',
            date=date(2025, 8, 10),
            published=True,
        )
        self.link = CuratedLink.objects.create(
            item_id='test-link',
            title='Test Link',
            description='Link desc',
            url='https://example.com',
            category='tools',
            published=True,
        )

    def test_home_template(self):
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'home.html')

    def test_home_contains_content(self):
        response = self.client.get('/')
        self.assertEqual(list(response.context['articles']), [self.article])
        self.assertEqual(list(response.context['recordings']), [self.recording])
        self.assertEqual(list(response.context['projects']), [self.project])
        self.assertEqual(list(response.context['curated_links']), [self.link])

    def test_home_contains_sections(self):
        response = self.client.get('/')
        for section_id in (
            'about',
            'tiers',
            'testimonials',
            'resources',
            'blog',
            'collection',
            'newsletter',
            'faq',
        ):
            self.assertContains(response, f'id="{section_id}"')

    def test_home_testimonials_use_balanced_grid(self):
        response = self.client.get('/')
        self.assertContains(response, 'data-testid="testimonial-grid"')
        self.assertContains(response, 'data-testid="testimonial-card"')
        self.assertNotContains(response, '<footer class="mt-6 flex')
        self.assertContains(response, 'md:grid-cols-2')
        self.assertNotContains(response, 'columns-1')

    def test_home_unpublished_not_shown(self):
        Article.objects.create(
            title='Unpublished Article',
            slug='unpub-article',
            date=date(2025, 1, 1),
            published=False,
        )
        response = self.client.get('/')
        self.assertNotContains(response, 'Unpublished Article')


class AboutViewTest(TestCase):
    def test_about_template(self):
        response = self.client.get('/about')
        self.assertTemplateUsed(response, 'content/about.html')

    def test_about_contains_content(self):
        response = self.client.get('/about')
        self.assertContains(response, 'About AI Shipping Labs')
        self.assertContains(response, 'Alexey Grigorev')
        self.assertContains(response, 'Valeriia Kuka')
        self.assertContains(response, 'Co-founder')


class ActivitiesViewTest(TestCase):
    def test_activities_template(self):
        response = self.client.get('/activities')
        self.assertTemplateUsed(response, 'content/activities.html')

    def test_activities_contains_content(self):
        response = self.client.get('/activities')
        self.assertContains(response, 'Active community sprints')
        self.assertContains(response, 'Next sprint coming soon')
        self.assertContains(response, 'Quick comparison')


class BlogListViewTest(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            title='Blog Post 1',
            slug='blog-post-1',
            description='First post',
            date=date(2025, 6, 15),
            tags=['test'],
            published=True,
        )

    def test_blog_list_template(self):
        response = self.client.get('/blog')
        self.assertTemplateUsed(response, 'content/blog_list.html')

    def test_blog_list_contains_article(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'Blog Post 1')
        self.assertContains(response, 'First post')

    def test_blog_list_empty(self):
        Article.objects.all().delete()
        response = self.client.get('/blog')
        # Post-launch empty-state copy (issue #319).
        self.assertContains(response, 'No articles match this filter yet')


class BlogDetailViewTest(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            title='Detail Post',
            slug='detail-post',
            description='Detailed description',
            content_html='<p>Full content here</p>',
            date=date(2025, 6, 15),
            tags=['python', 'ai'],
            reading_time='5 min read',
            published=True,
        )

    def test_blog_detail_template(self):
        response = self.client.get('/blog/detail-post')
        self.assertTemplateUsed(response, 'content/blog_detail.html')

    def test_blog_detail_contains_content(self):
        response = self.client.get('/blog/detail-post')
        self.assertEqual(response.context['article'], self.article)
        self.assertContains(response, '<p>Full content here</p>', html=True)
        self.assertContains(response, '5 min read')

    def test_blog_detail_404(self):
        response = self.client.get('/blog/nonexistent-post')
        self.assertEqual(response.status_code, 404)

    def test_blog_detail_unpublished_404(self):
        Article.objects.create(
            title='Unpublished',
            slug='unpublished',
            date=date(2025, 1, 1),
            published=False,
        )
        response = self.client.get('/blog/unpublished')
        self.assertEqual(response.status_code, 404)


class RecordingsListViewTest(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.recording = Event.objects.create(
            title='Workshop 1',
            slug='workshop-1',
            description='First workshop',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            tags=['agents'],
            published=True,
        )

    def test_recordings_list_template(self):
        response = self.client.get('/events?filter=past')
        self.assertTemplateUsed(response, 'events/events_list.html')

    def test_recordings_list_contains_recording(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'Workshop 1')

    def test_recordings_list_empty(self):
        Event.objects.all().delete()
        response = self.client.get('/events?filter=past')
        self.assertContains(response, 'No recordings yet')


class RecordingDetailViewTest(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.recording = Event.objects.create(
            title='Workshop Detail',
            slug='workshop-detail',
            description='Workshop description',
            start_datetime=timezone.now(),
            status='completed',
            tags=['ai'],
            recording_url='https://youtube.com/watch?v=test',
            timestamps=[{'time': '00:00', 'title': 'Intro', 'description': 'Introduction'}],
            materials=[{'title': 'Slides', 'url': 'https://example.com/slides', 'type': 'slides'}],
            core_tools=['Python'],
            learning_objectives=['Learn basics'],
            outcome='Build something',
            published=True,
        )

    def test_recording_detail_template(self):
        response = self.client.get('/events/workshop-detail')
        self.assertTemplateUsed(response, 'events/event_detail.html')

    def test_recording_detail_contains_content(self):
        # Issue #426: event detail is announcement-only. Title and
        # description still render; recording-only fields (Core Tools,
        # learning objectives, expected outcome, materials, timestamps)
        # are not rendered here — they live on the linked Workshop's
        # video page.
        response = self.client.get('/events/workshop-detail')
        self.assertEqual(response.context['event'], self.recording)
        self.assertContains(response, 'Workshop description')
        self.assertNotContains(response, 'Core Tools')
        self.assertNotContains(response, 'Python')
        self.assertNotContains(response, 'Learn basics')
        self.assertNotContains(response, 'Build something')
        self.assertNotContains(response, 'https://example.com/slides')

    def test_recording_detail_404(self):
        response = self.client.get('/events/nonexistent')
        self.assertEqual(response.status_code, 404)


class ProjectsListViewTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            title='Project 1',
            slug='project-1',
            description='First project',
            date=date(2025, 8, 10),
            difficulty='beginner',
            published=True,
        )

    def test_projects_list_template(self):
        response = self.client.get('/projects')
        self.assertTemplateUsed(response, 'content/projects_list.html')

    def test_projects_list_contains_project(self):
        response = self.client.get('/projects')
        self.assertContains(response, 'Project 1')

    def test_projects_list_empty(self):
        Project.objects.all().delete()
        response = self.client.get('/projects')
        self.assertContains(response, 'No project ideas yet')


class ProjectDetailViewTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            title='Project Detail',
            slug='project-detail',
            description='Project desc',
            content_html='<p>Project content</p>',
            date=date(2025, 8, 10),
            author='Builder',
            difficulty='intermediate',
            tags=['ai'],
            published=True,
        )

    def test_project_detail_template(self):
        response = self.client.get('/projects/project-detail')
        self.assertTemplateUsed(response, 'content/project_detail.html')

    def test_project_detail_contains_content(self):
        response = self.client.get('/projects/project-detail')
        self.assertEqual(response.context['project'], self.project)
        self.assertContains(response, '<p>Project content</p>', html=True)
        self.assertContains(response, 'by Builder')

    def test_project_detail_404(self):
        response = self.client.get('/projects/nonexistent')
        self.assertEqual(response.status_code, 404)


class CollectionListViewTest(TestCase):
    def setUp(self):
        self.link = CuratedLink.objects.create(
            item_id='test-tool',
            title='Test Tool',
            description='A tool',
            url='https://example.com/tool',
            category='tools',
            source='GitHub',
            published=True,
        )

    def test_collection_list_template(self):
        response = self.client.get('/resources')
        self.assertTemplateUsed(response, 'content/collection_list.html')

    def test_collection_list_contains_link(self):
        response = self.client.get('/resources')
        self.assertContains(response, 'Test Tool')
        self.assertContains(response, 'A tool')
        self.assertContains(response, 'GitHub')


class TutorialsListViewTest(TestCase):
    def test_tutorials_list_template(self):
        response = self.client.get('/tutorials')
        self.assertTemplateUsed(response, 'content/tutorials_list.html')

    def test_tutorials_list_empty(self):
        response = self.client.get('/tutorials')
        self.assertContains(response, 'No tutorials yet')

    def test_tutorials_list_with_content(self):
        Tutorial.objects.create(
            title='Tutorial 1',
            slug='tutorial-1',
            description='Learn something',
            date=date(2025, 9, 1),
            published=True,
        )
        response = self.client.get('/tutorials')
        self.assertContains(response, 'Tutorial 1')


class TutorialDetailViewTest(TestCase):
    def setUp(self):
        self.tutorial = Tutorial.objects.create(
            title='Tutorial Detail',
            slug='tutorial-detail',
            description='Tutorial desc',
            content_html='<p>Tutorial content</p>',
            date=date(2025, 9, 1),
            tags=['python'],
            reading_time='10 min read',
            published=True,
        )

    def test_tutorial_detail_template(self):
        response = self.client.get('/tutorials/tutorial-detail')
        self.assertTemplateUsed(response, 'content/tutorial_detail.html')

    def test_tutorial_detail_contains_content(self):
        response = self.client.get('/tutorials/tutorial-detail')
        self.assertEqual(response.context['tutorial'], self.tutorial)
        self.assertContains(response, '<p>Tutorial content</p>', html=True)
        self.assertContains(response, 'python')

    def test_tutorial_detail_404(self):
        response = self.client.get('/tutorials/nonexistent')
        self.assertEqual(response.status_code, 404)
