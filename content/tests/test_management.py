from django.test import TestCase
from django.core.management import call_command
from io import StringIO

from content.models import Article, Recording, Project, CuratedLink


class LoadContentCommandTest(TestCase):
    def test_load_content(self):
        """Test that load_content command runs without errors and populates models."""
        out = StringIO()
        call_command('load_content', stdout=out)

        output = out.getvalue()
        self.assertIn('Content loaded successfully', output)

        # Check that content was loaded
        self.assertGreater(Article.objects.count(), 0)
        self.assertGreater(Recording.objects.count(), 0)
        self.assertGreater(Project.objects.count(), 0)
        self.assertGreater(CuratedLink.objects.count(), 0)

    def test_load_content_idempotent(self):
        """Test that running load_content twice doesn't create duplicates."""
        call_command('load_content', stdout=StringIO())
        count1 = Article.objects.count()
        call_command('load_content', stdout=StringIO())
        count2 = Article.objects.count()
        self.assertEqual(count1, count2)

    def test_articles_have_content(self):
        """Test that loaded articles have HTML content."""
        call_command('load_content', stdout=StringIO())
        for article in Article.objects.all():
            self.assertTrue(article.title)
            self.assertTrue(article.slug)
            self.assertTrue(article.content_html)
            self.assertTrue(article.reading_time)

    def test_curated_links_count(self):
        """Test that all curated links are loaded."""
        call_command('load_content', stdout=StringIO())
        self.assertEqual(CuratedLink.objects.count(), 27)

    def test_curated_links_categories(self):
        """Test that curated links have correct categories."""
        call_command('load_content', stdout=StringIO())
        categories = set(CuratedLink.objects.values_list('category', flat=True))
        self.assertIn('tools', categories)
        self.assertIn('courses', categories)
        self.assertIn('other', categories)
