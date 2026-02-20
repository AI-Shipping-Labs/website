from datetime import date
from django.test import TestCase
from content.models import Article, Recording, Project, Tutorial, CuratedLink


class ArticleModelTest(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            title='Test Article',
            slug='test-article',
            description='A test article description',
            content_markdown='# Hello\nThis is test content.',
            content_html='<h1>Hello</h1><p>This is test content.</p>',
            date=date(2025, 6, 15),
            author='Test Author',
            reading_time='5 min read',
            tags=['python', 'ai'],
            published=True,
        )

    def test_str(self):
        self.assertEqual(str(self.article), 'Test Article')

    def test_get_absolute_url(self):
        self.assertEqual(self.article.get_absolute_url(), '/blog/test-article')

    def test_formatted_date(self):
        self.assertEqual(self.article.formatted_date(), 'June 15, 2025')

    def test_short_date(self):
        self.assertEqual(self.article.short_date(), 'Jun 15, 2025')

    def test_ordering(self):
        Article.objects.create(
            title='Older Article',
            slug='older-article',
            date=date(2025, 1, 1),
        )
        articles = list(Article.objects.all())
        self.assertEqual(articles[0].slug, 'test-article')
        self.assertEqual(articles[1].slug, 'older-article')

    def test_default_values(self):
        article = Article.objects.create(
            title='Minimal Article',
            slug='minimal-article',
            date=date(2025, 1, 1),
        )
        self.assertEqual(article.description, '')
        self.assertEqual(article.tags, [])
        self.assertTrue(article.published)

    def test_unique_slug(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Article.objects.create(
                title='Duplicate',
                slug='test-article',
                date=date(2025, 1, 1),
            )


class RecordingModelTest(TestCase):
    def setUp(self):
        self.recording = Recording.objects.create(
            title='Test Recording',
            slug='test-recording',
            description='A test recording',
            date=date(2025, 7, 20),
            tags=['workshop', 'agents'],
            level='Beginner',
            youtube_url='https://youtube.com/watch?v=test',
            timestamps=[{'time': '00:00', 'title': 'Intro'}],
            materials=[{'title': 'Slides', 'url': 'https://example.com/slides', 'type': 'slides'}],
            core_tools=['Python', 'Django'],
            learning_objectives=['Learn Django basics'],
            outcome='Build a web app',
            published=True,
        )

    def test_str(self):
        self.assertEqual(str(self.recording), 'Test Recording')

    def test_get_absolute_url(self):
        self.assertEqual(self.recording.get_absolute_url(), '/event-recordings/test-recording')

    def test_formatted_date(self):
        self.assertEqual(self.recording.formatted_date(), 'July 20, 2025')

    def test_short_date(self):
        self.assertEqual(self.recording.short_date(), 'Jul 20, 2025')

    def test_json_fields(self):
        self.assertEqual(len(self.recording.timestamps), 1)
        self.assertEqual(self.recording.timestamps[0]['title'], 'Intro')
        self.assertEqual(len(self.recording.materials), 1)
        self.assertEqual(len(self.recording.core_tools), 2)
        self.assertEqual(len(self.recording.learning_objectives), 1)

    def test_default_values(self):
        rec = Recording.objects.create(
            title='Minimal',
            slug='minimal-recording',
            date=date(2025, 1, 1),
        )
        self.assertEqual(rec.tags, [])
        self.assertEqual(rec.timestamps, [])
        self.assertEqual(rec.materials, [])
        self.assertEqual(rec.core_tools, [])
        self.assertEqual(rec.learning_objectives, [])
        self.assertEqual(rec.level, '')
        self.assertEqual(rec.outcome, '')


class ProjectModelTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            title='Test Project',
            slug='test-project',
            description='A test project description',
            content_markdown='# Project\nDetails here.',
            content_html='<h1>Project</h1><p>Details here.</p>',
            date=date(2025, 8, 10),
            author='Builder',
            tags=['ai', 'agents'],
            reading_time='3 min read',
            difficulty='intermediate',
            estimated_time='4 hours',
            published=True,
        )

    def test_str(self):
        self.assertEqual(str(self.project), 'Test Project')

    def test_get_absolute_url(self):
        self.assertEqual(self.project.get_absolute_url(), '/projects/test-project')

    def test_difficulty_color_intermediate(self):
        self.assertEqual(self.project.difficulty_color(), 'bg-yellow-500/20 text-yellow-400')

    def test_difficulty_color_beginner(self):
        self.project.difficulty = 'beginner'
        self.assertEqual(self.project.difficulty_color(), 'bg-green-500/20 text-green-400')

    def test_difficulty_color_advanced(self):
        self.project.difficulty = 'advanced'
        self.assertEqual(self.project.difficulty_color(), 'bg-red-500/20 text-red-400')

    def test_difficulty_color_empty(self):
        self.project.difficulty = ''
        self.assertEqual(self.project.difficulty_color(), 'bg-secondary text-muted-foreground')

    def test_formatted_date(self):
        self.assertEqual(self.project.formatted_date(), 'August 10, 2025')


class TutorialModelTest(TestCase):
    def setUp(self):
        self.tutorial = Tutorial.objects.create(
            title='Test Tutorial',
            slug='test-tutorial',
            description='Learn something',
            content_markdown='# Step 1',
            content_html='<h1>Step 1</h1>',
            date=date(2025, 9, 1),
            tags=['python'],
            reading_time='10 min read',
            published=True,
        )

    def test_str(self):
        self.assertEqual(str(self.tutorial), 'Test Tutorial')

    def test_get_absolute_url(self):
        self.assertEqual(self.tutorial.get_absolute_url(), '/tutorials/test-tutorial')

    def test_formatted_date(self):
        self.assertEqual(self.tutorial.formatted_date(), 'September 01, 2025')

    def test_short_date(self):
        self.assertEqual(self.tutorial.short_date(), 'Sep 01, 2025')


class CuratedLinkModelTest(TestCase):
    def setUp(self):
        self.link = CuratedLink.objects.create(
            item_id='test-link',
            title='Test Link',
            description='A test curated link',
            url='https://example.com/test',
            category='tools',
            source='GitHub',
            sort_order=0,
            published=True,
        )

    def test_str(self):
        self.assertEqual(str(self.link), 'Test Link')

    def test_category_label(self):
        self.assertEqual(self.link.category_label, 'Tools')

    def test_category_label_models(self):
        self.link.category = 'models'
        self.assertEqual(self.link.category_label, 'Models')

    def test_category_label_courses(self):
        self.link.category = 'courses'
        self.assertEqual(self.link.category_label, 'Courses')

    def test_category_label_other(self):
        self.link.category = 'other'
        self.assertEqual(self.link.category_label, 'Other')

    def test_is_external(self):
        self.assertTrue(self.link.is_external)

    def test_is_not_external(self):
        self.link.url = '/internal/path'
        self.assertFalse(self.link.is_external)

    def test_category_icon_name(self):
        self.assertEqual(self.link.category_icon_name, 'wrench')
        self.link.category = 'models'
        self.assertEqual(self.link.category_icon_name, 'cpu')
        self.link.category = 'courses'
        self.assertEqual(self.link.category_icon_name, 'graduation-cap')
        self.link.category = 'other'
        self.assertEqual(self.link.category_icon_name, 'folder-open')

    def test_unique_item_id(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CuratedLink.objects.create(
                item_id='test-link',
                title='Duplicate',
                url='https://example.com',
                category='tools',
            )

    def test_ordering(self):
        CuratedLink.objects.create(
            item_id='second-link',
            title='Second Link',
            url='https://example.com/2',
            category='tools',
            sort_order=1,
        )
        links = list(CuratedLink.objects.all())
        self.assertEqual(links[0].item_id, 'test-link')
        self.assertEqual(links[1].item_id, 'second-link')
