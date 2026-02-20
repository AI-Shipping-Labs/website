from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from content.models import Article, Recording, Project, Tutorial, CuratedLink


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
        self.recording = Recording.objects.create(
            title='Test Recording',
            slug='test-recording',
            description='Workshop desc',
            date=date(2025, 7, 20),
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

    def test_home_status_code(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_home_template(self):
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'home.html')

    def test_home_contains_content(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Turn AI ideas into', content)
        self.assertIn('real projects', content)
        self.assertIn('Test Article', content)
        self.assertIn('Test Recording', content)
        self.assertIn('Test Project', content)
        self.assertIn('Test Link', content)

    def test_home_contains_sections(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('id="about"', content)
        self.assertIn('id="tiers"', content)
        self.assertIn('id="testimonials"', content)
        self.assertIn('id="resources"', content)
        self.assertIn('id="blog"', content)
        self.assertIn('id="collection"', content)
        self.assertIn('id="newsletter"', content)
        self.assertIn('id="faq"', content)

    def test_home_contains_testimonials(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Rolando', content)
        self.assertIn('AI Data Scientist', content)

    def test_home_contains_tiers(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Basic', content)
        self.assertIn('Main', content)
        self.assertIn('Premium', content)
        self.assertIn('Most Popular', content)

    def test_home_contains_faq(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('Who is this community for?', content)
        self.assertIn('How do I get started?', content)

    def test_home_contains_nav(self):
        response = self.client.get('/')
        content = response.content.decode()
        self.assertIn('AI Shipping Labs', content)
        self.assertIn('/about', content)
        self.assertIn('/activities', content)
        self.assertIn('/blog', content)

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
    def test_about_status_code(self):
        response = self.client.get('/about')
        self.assertEqual(response.status_code, 200)

    def test_about_template(self):
        response = self.client.get('/about')
        self.assertTemplateUsed(response, 'content/about.html')

    def test_about_contains_content(self):
        response = self.client.get('/about')
        content = response.content.decode()
        self.assertIn('About AI Shipping Labs', content)
        self.assertIn('Alexey Grigorev', content)
        self.assertIn('Valeriia Kuka', content)
        self.assertIn('Co-founder', content)


class ActivitiesViewTest(TestCase):
    def test_activities_status_code(self):
        response = self.client.get('/activities')
        self.assertEqual(response.status_code, 200)

    def test_activities_template(self):
        response = self.client.get('/activities')
        self.assertTemplateUsed(response, 'content/activities.html')

    def test_activities_contains_content(self):
        response = self.client.get('/activities')
        content = response.content.decode()
        self.assertIn('Activities and access by tier', content)
        self.assertIn('Exclusive Substack Content', content)
        self.assertIn('Quick comparison', content)


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

    def test_blog_list_status_code(self):
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)

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
        self.assertContains(response, 'No posts yet')


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

    def test_blog_detail_status_code(self):
        response = self.client.get('/blog/detail-post')
        self.assertEqual(response.status_code, 200)

    def test_blog_detail_template(self):
        response = self.client.get('/blog/detail-post')
        self.assertTemplateUsed(response, 'content/blog_detail.html')

    def test_blog_detail_contains_content(self):
        response = self.client.get('/blog/detail-post')
        content = response.content.decode()
        self.assertIn('Detail Post', content)
        self.assertIn('Detailed description', content)
        self.assertIn('Full content here', content)
        self.assertIn('python', content)
        self.assertIn('5 min read', content)

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
        self.recording = Recording.objects.create(
            title='Workshop 1',
            slug='workshop-1',
            description='First workshop',
            date=date(2025, 7, 20),
            tags=['agents'],
            published=True,
        )

    def test_recordings_list_status_code(self):
        response = self.client.get('/event-recordings')
        self.assertEqual(response.status_code, 200)

    def test_recordings_list_template(self):
        response = self.client.get('/event-recordings')
        self.assertTemplateUsed(response, 'content/recordings_list.html')

    def test_recordings_list_contains_recording(self):
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'Workshop 1')

    def test_recordings_list_empty(self):
        Recording.objects.all().delete()
        response = self.client.get('/event-recordings')
        self.assertContains(response, 'No resources yet')


class RecordingDetailViewTest(TestCase):
    def setUp(self):
        self.recording = Recording.objects.create(
            title='Workshop Detail',
            slug='workshop-detail',
            description='Workshop description',
            date=date(2025, 7, 20),
            level='Beginner',
            tags=['ai'],
            youtube_url='https://youtube.com/watch?v=test',
            timestamps=[{'time': '00:00', 'title': 'Intro', 'description': 'Introduction'}],
            materials=[{'title': 'Slides', 'url': 'https://example.com/slides', 'type': 'slides'}],
            core_tools=['Python'],
            learning_objectives=['Learn basics'],
            outcome='Build something',
            published=True,
        )

    def test_recording_detail_status_code(self):
        response = self.client.get('/event-recordings/workshop-detail')
        self.assertEqual(response.status_code, 200)

    def test_recording_detail_template(self):
        response = self.client.get('/event-recordings/workshop-detail')
        self.assertTemplateUsed(response, 'content/recording_detail.html')

    def test_recording_detail_contains_content(self):
        response = self.client.get('/event-recordings/workshop-detail')
        content = response.content.decode()
        self.assertIn('Workshop Detail', content)
        self.assertIn('Workshop description', content)
        self.assertIn('Beginner', content)
        self.assertIn('Core Tools', content)
        self.assertIn('Python', content)
        self.assertIn('Learn basics', content)
        self.assertIn('Build something', content)
        self.assertIn('Slides', content)
        self.assertIn('00:00', content)

    def test_recording_detail_404(self):
        response = self.client.get('/event-recordings/nonexistent')
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

    def test_projects_list_status_code(self):
        response = self.client.get('/projects')
        self.assertEqual(response.status_code, 200)

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

    def test_project_detail_status_code(self):
        response = self.client.get('/projects/project-detail')
        self.assertEqual(response.status_code, 200)

    def test_project_detail_template(self):
        response = self.client.get('/projects/project-detail')
        self.assertTemplateUsed(response, 'content/project_detail.html')

    def test_project_detail_contains_content(self):
        response = self.client.get('/projects/project-detail')
        content = response.content.decode()
        self.assertIn('Project Detail', content)
        self.assertIn('Project desc', content)
        self.assertIn('Project content', content)
        self.assertIn('by Builder', content)

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

    def test_collection_list_status_code(self):
        response = self.client.get('/collection')
        self.assertEqual(response.status_code, 200)

    def test_collection_list_template(self):
        response = self.client.get('/collection')
        self.assertTemplateUsed(response, 'content/collection_list.html')

    def test_collection_list_contains_link(self):
        response = self.client.get('/collection')
        self.assertContains(response, 'Test Tool')
        self.assertContains(response, 'A tool')
        self.assertContains(response, 'GitHub')


class TutorialsListViewTest(TestCase):
    def test_tutorials_list_status_code(self):
        response = self.client.get('/tutorials')
        self.assertEqual(response.status_code, 200)

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

    def test_tutorial_detail_status_code(self):
        response = self.client.get('/tutorials/tutorial-detail')
        self.assertEqual(response.status_code, 200)

    def test_tutorial_detail_template(self):
        response = self.client.get('/tutorials/tutorial-detail')
        self.assertTemplateUsed(response, 'content/tutorial_detail.html')

    def test_tutorial_detail_contains_content(self):
        response = self.client.get('/tutorials/tutorial-detail')
        content = response.content.decode()
        self.assertIn('Tutorial Detail', content)
        self.assertIn('Tutorial content', content)
        self.assertIn('python', content)

    def test_tutorial_detail_404(self):
        response = self.client.get('/tutorials/nonexistent')
        self.assertEqual(response.status_code, 404)
