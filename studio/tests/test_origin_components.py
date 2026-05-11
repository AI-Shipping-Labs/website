"""Tests for Studio provenance helper partials."""

from django.template import Context, Template
from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course, Download, Project
from events.models import Event


class StudioOriginComponentsTest(TestCase):
    def _render(self, template_source, **context):
        template = Template(
            '{% load studio_filters %}' + template_source,
        )
        return template.render(Context(context))

    def test_origin_badge_renders_synced_source_hint(self):
        course = Course.objects.create(
            title='Synced',
            slug='synced',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced/course.yaml',
        )

        html = self._render('{% studio_origin_badge course %}', course=course)

        self.assertIn('Synced', html)
        self.assertIn('courses/synced/course.yaml', html)
        self.assertIn(
            'https://github.com/AI-Shipping-Labs/content/blob/main/'
            'courses/synced/course.yaml',
            html,
        )

    def test_origin_badge_renders_local_copy(self):
        course = Course.objects.create(title='Local', slug='local')

        html = self._render('{% studio_origin_badge course %}', course=course)

        self.assertIn('Local / manual', html)
        self.assertIn('No GitHub source metadata', html)

    def test_origin_panel_hides_github_actions_for_local_rows(self):
        course = Course.objects.create(title='Local', slug='local')

        html = self._render('{% studio_origin_panel course %}', course=course)

        self.assertIn('Local / manual', html)
        self.assertIn('No GitHub source metadata exists', html)
        self.assertNotIn('Edit on GitHub', html)
        self.assertNotIn('Re-sync source', html)

    def test_origin_panel_renders_article_source_metadata(self):
        article = Article.objects.create(
            title='Synced Article',
            slug='synced-article',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='articles/synced-article.md',
            source_commit='abc123def4567890',
        )

        html = self._render('{% studio_origin_panel article %}', article=article)

        self.assertIn('data-testid="origin-panel"', html)
        self.assertIn('Synced from GitHub', html)
        self.assertIn('AI-Shipping-Labs/content', html)
        self.assertIn('articles/synced-article.md', html)
        self.assertIn('Edit on GitHub', html)
        self.assertIn('Re-sync source', html)

    def test_origin_panel_renders_download_source_metadata(self):
        download = Download.objects.create(
            title='Synced Download',
            slug='synced-download',
            file_url='https://example.com/file.pdf',
            source_repo='AI-Shipping-Labs/content',
            source_path='downloads/synced-download.yaml',
            source_commit='def456abc7890123',
        )

        html = self._render(
            '{% studio_origin_panel download %}',
            download=download,
        )

        self.assertIn('data-testid="origin-panel"', html)
        self.assertIn('AI-Shipping-Labs/content', html)
        self.assertIn('downloads/synced-download.yaml', html)
        self.assertIn('Edit on GitHub', html)
        self.assertIn('Re-sync source', html)

    def test_origin_panel_renders_event_source_metadata(self):
        event = Event.objects.create(
            title='Synced Event',
            slug='synced-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced-event.md',
            source_commit='7890123def456abc',
        )

        html = self._render('{% studio_origin_panel event %}', event=event)

        self.assertIn('data-testid="origin-panel"', html)
        self.assertIn('AI-Shipping-Labs/content', html)
        self.assertIn('events/synced-event.md', html)
        self.assertIn('Edit on GitHub', html)
        self.assertIn('Re-sync source', html)

    def test_origin_panel_renders_project_source_metadata(self):
        project = Project.objects.create(
            title='Synced Project',
            slug='synced-project',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='projects/synced-project.md',
            source_commit='0123def456abc789',
        )

        html = self._render(
            '{% studio_origin_panel project %}',
            project=project,
        )

        self.assertIn('data-testid="origin-panel"', html)
        self.assertIn('AI-Shipping-Labs/content', html)
        self.assertIn('projects/synced-project.md', html)
        self.assertIn('Edit on GitHub', html)
        self.assertIn('Re-sync source', html)
