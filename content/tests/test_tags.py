"""Tests for Tags, Filtering, and Conditional Components - issue #91.

Covers:
- Tag normalization on save for all content models
- /tags index page showing all tags with counts
- /tags/{tag} detail page showing cross-type content
- Multi-tag filtering with AND logic on listing pages
- Active tag filters shown as removable chips
- TagRule model CRUD and admin
- TagRule component injection on content detail pages
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.models import (
    Article,
    Course,
    CuratedLink,
    Download,
    Project,
    TagRule,
    Tutorial,
)
from content.utils.tags import normalize_tag, normalize_tags
from events.models import Event

User = get_user_model()


# --- Tag Normalization Tests ---


class NormalizeTagTest(TestCase):
    """Test normalize_tag utility function."""

    def test_lowercase(self):
        self.assertEqual(normalize_tag('Python'), 'python')

    def test_spaces_to_hyphens(self):
        self.assertEqual(normalize_tag('Machine Learning'), 'machine-learning')

    def test_special_characters_removed(self):
        self.assertEqual(normalize_tag('AI & ML'), 'ai-ml')

    def test_dots_removed(self):
        self.assertEqual(normalize_tag('Python 3.12'), 'python-312')

    def test_underscores_to_hyphens(self):
        self.assertEqual(normalize_tag('data_science'), 'data-science')

    def test_multiple_spaces_collapsed(self):
        self.assertEqual(normalize_tag('  hello  world  '), 'hello-world')

    def test_leading_trailing_hyphens_stripped(self):
        self.assertEqual(normalize_tag('-python-'), 'python')

    def test_empty_string(self):
        self.assertEqual(normalize_tag(''), '')

    def test_none(self):
        self.assertEqual(normalize_tag(None), '')

    def test_already_normalized(self):
        self.assertEqual(normalize_tag('ai-engineering'), 'ai-engineering')

    def test_mixed_case_with_special(self):
        self.assertEqual(normalize_tag('AI/ML Engineering!'), 'aiml-engineering')


class NormalizeTagsTest(TestCase):
    """Test normalize_tags utility function."""

    def test_basic_list(self):
        result = normalize_tags(['Python', 'AI', 'Machine Learning'])
        self.assertEqual(result, ['python', 'ai', 'machine-learning'])

    def test_removes_duplicates_after_normalization(self):
        result = normalize_tags(['Python', 'python', 'PYTHON'])
        self.assertEqual(result, ['python'])

    def test_preserves_order(self):
        result = normalize_tags(['zeta', 'alpha', 'beta'])
        self.assertEqual(result, ['zeta', 'alpha', 'beta'])

    def test_removes_empty_after_normalization(self):
        result = normalize_tags(['python', '', '   ', '!!!'])
        self.assertEqual(result, ['python'])

    def test_empty_list(self):
        self.assertEqual(normalize_tags([]), [])

    def test_none(self):
        self.assertEqual(normalize_tags(None), [])


# --- Tag Normalization on Save Tests ---


class ArticleTagNormalizationTest(TestCase):
    """Test that Article normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        article = Article.objects.create(
            title='Test', slug='test-norm', date=date(2025, 1, 1),
            tags=['Machine Learning', 'AI & ML', 'Python'],
            published=True,
        )
        self.assertEqual(article.tags, ['machine-learning', 'ai-ml', 'python'])

    def test_duplicate_tags_removed(self):
        article = Article.objects.create(
            title='Test', slug='test-dedup', date=date(2025, 1, 1),
            tags=['Python', 'python', 'PYTHON'],
            published=True,
        )
        self.assertEqual(article.tags, ['python'])


class RecordingTagNormalizationTest(TestCase):
    """Test that Recording normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        recording = Event.objects.create(
            title='Test', slug='test-rec-norm', start_datetime=timezone.make_aware(timezone.datetime(2025, 1, 1, 12, 0)), status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['Data Science', 'AI Engineering'],
            published=True,
        )
        self.assertEqual(recording.tags, ['data-science', 'ai-engineering'])


class ProjectTagNormalizationTest(TestCase):
    """Test that Project normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        project = Project.objects.create(
            title='Test', slug='test-proj-norm', date=date(2025, 1, 1),
            tags=['Natural Language Processing'],
            published=True,
        )
        self.assertEqual(project.tags, ['natural-language-processing'])


class TutorialTagNormalizationTest(TestCase):
    """Test that Tutorial normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        tutorial = Tutorial.objects.create(
            title='Test', slug='test-tut-norm', date=date(2025, 1, 1),
            tags=['Deep Learning'],
            published=True,
        )
        self.assertEqual(tutorial.tags, ['deep-learning'])


class CuratedLinkTagNormalizationTest(TestCase):
    """Test that CuratedLink normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        link = CuratedLink.objects.create(
            item_id='test-link-norm', title='Test',
            url='https://example.com', category='tools',
            tags=['Machine Learning'],
            published=True,
        )
        self.assertEqual(link.tags, ['machine-learning'])


class DownloadTagNormalizationTest(TestCase):
    """Test that Download normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        download = Download.objects.create(
            title='Test', slug='test-dl-norm',
            file_url='https://example.com/file.pdf',
            tags=['Data Analysis'],
            published=True,
        )
        self.assertEqual(download.tags, ['data-analysis'])


class CourseTagNormalizationTest(TestCase):
    """Test that Course normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        course = Course.objects.create(
            title='Test', slug='test-course-norm',
            tags=['AI Engineering'],
            status='published',
        )
        self.assertEqual(course.tags, ['ai-engineering'])


class EventTagNormalizationTest(TestCase):
    """Test that Event normalizes tags on save."""

    def test_tags_normalized_on_save(self):
        event = Event.objects.create(
            title='Test', slug='test-event-norm',
            start_datetime=timezone.now(),
            tags=['Live Coding'],
        )
        self.assertEqual(event.tags, ['live-coding'])


# --- Tags Index Page Tests ---


class TagsIndexViewTest(TestCase):
    """Test GET /tags shows all tags with content counts."""

    def setUp(self):
        self.client = Client()
        Article.objects.create(
            title='A1', slug='a1', date=date(2025, 1, 1),
            tags=['python', 'ai'], published=True,
        )
        Article.objects.create(
            title='A2', slug='a2', date=date(2025, 1, 2),
            tags=['python'], published=True,
        )
        Event.objects.create(
            title='R1', slug='r1', start_datetime=timezone.make_aware(timezone.datetime(2025, 1, 1, 12, 0)), status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['ai', 'workshop'], published=True,
        )

    def test_tags_page_returns_200(self):
        response = self.client.get('/tags')
        self.assertEqual(response.status_code, 200)

    def test_tags_page_shows_all_tags(self):
        response = self.client.get('/tags')
        content = response.content.decode()
        self.assertIn('python', content)
        self.assertIn('ai', content)
        self.assertIn('workshop', content)

    def test_tags_sorted_by_count_descending(self):
        response = self.client.get('/tags')
        tag_counts = response.context['tag_counts']
        # python=2, ai=2, workshop=1
        counts = [count for tag, count in tag_counts]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_tags_page_shows_counts(self):
        response = self.client.get('/tags')
        tag_counts = response.context['tag_counts']
        tag_dict = dict(tag_counts)
        self.assertEqual(tag_dict['python'], 2)
        self.assertEqual(tag_dict['workshop'], 1)

    def test_tags_page_title(self):
        response = self.client.get('/tags')
        content = response.content.decode()
        self.assertIn('<title>Tags | AI Shipping Labs</title>', content)

    def test_no_tags_shows_empty_message(self):
        Article.objects.all().delete()
        Event.objects.all().delete()
        response = self.client.get('/tags')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No tags yet')


# --- Tags Detail Page Tests ---


class TagsDetailViewTest(TestCase):
    """Test GET /tags/{tag} shows all content with that tag."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Python Article', slug='python-art', date=date(2025, 6, 15),
            tags=['python', 'tutorial'], published=True,
            description='Python article description',
        )
        cls.recording = Event.objects.create(
            title='Python Workshop', slug='python-ws', start_datetime=timezone.make_aware(timezone.datetime(2025, 6, 10, 12, 0)), status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['python', 'workshop'], published=True,
            description='Workshop description',
        )
        cls.project = Project.objects.create(
            title='AI Project', slug='ai-proj', date=date(2025, 6, 5),
            tags=['ai'], published=True,
            description='AI project description',
        )

    def test_tags_detail_returns_200(self):
        response = self.client.get('/tags/python')
        self.assertEqual(response.status_code, 200)

    def test_shows_content_with_tag(self):
        response = self.client.get('/tags/python')
        self.assertContains(response, 'Python Article')
        self.assertContains(response, 'Python Workshop')

    def test_does_not_show_content_without_tag(self):
        response = self.client.get('/tags/python')
        self.assertNotContains(response, 'AI Project')

    def test_shows_content_type_badges(self):
        response = self.client.get('/tags/python')
        self.assertContains(response, 'Article')
        self.assertContains(response, 'Event')

    def test_results_sorted_by_date_descending(self):
        import datetime as dt
        response = self.client.get('/tags/python')
        results = response.context['results']
        # Normalize dates for comparison (mix of date and datetime)
        def _norm(val):
            if isinstance(val, dt.datetime):
                return val
            return dt.datetime.combine(val, dt.time.min, tzinfo=dt.timezone.utc)
        dates = [_norm(r['date']) for r in results]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_shows_result_count(self):
        response = self.client.get('/tags/python')
        self.assertEqual(response.context['result_count'], 2)

    def test_nonexistent_tag_shows_empty(self):
        response = self.client.get('/tags/nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result_count'], 0)
        self.assertContains(response, 'No content found')

    def test_tag_page_title(self):
        response = self.client.get('/tags/python')
        content = response.content.decode()
        self.assertIn('<title>Tag: python | AI Shipping Labs</title>', content)

    def test_unpublished_content_excluded(self):
        Article.objects.create(
            title='Draft Python', slug='draft-python', date=date(2025, 6, 1),
            tags=['python'], published=False,
        )
        response = self.client.get('/tags/python')
        self.assertNotContains(response, 'Draft Python')


# --- Multi-Tag Filtering Tests ---


class MultiTagFilteringBlogTest(TestCase):
    """Test multi-tag AND filtering on /blog."""

    @classmethod
    def setUpTestData(cls):
        cls.both = Article.objects.create(
            title='Both Tags', slug='both-tags', date=date(2025, 6, 15),
            tags=['python', 'ai'], published=True,
        )
        cls.python_only = Article.objects.create(
            title='Python Only', slug='python-only', date=date(2025, 6, 14),
            tags=['python'], published=True,
        )
        cls.ai_only = Article.objects.create(
            title='AI Only', slug='ai-only', date=date(2025, 6, 13),
            tags=['ai'], published=True,
        )

    def test_single_tag_filter(self):
        response = self.client.get('/blog?tag=python')
        self.assertContains(response, 'Both Tags')
        self.assertContains(response, 'Python Only')
        self.assertNotContains(response, 'AI Only')

    def test_multi_tag_and_filter(self):
        response = self.client.get('/blog?tag=python&tag=ai')
        self.assertContains(response, 'Both Tags')
        self.assertNotContains(response, 'Python Only')
        self.assertNotContains(response, 'AI Only')

    def test_selected_tags_in_context(self):
        response = self.client.get('/blog?tag=python&tag=ai')
        self.assertEqual(sorted(response.context['selected_tags']), ['ai', 'python'])

    def test_filtering_returns_correct_results(self):
        response = self.client.get('/blog?tag=python&tag=ai')
        self.assertContains(response, 'Both Tags')
        self.assertNotContains(response, 'Python Only')
        self.assertNotContains(response, 'AI Only')

    def test_no_tags_shows_all(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'Both Tags')
        self.assertContains(response, 'Python Only')
        self.assertContains(response, 'AI Only')


class MultiTagFilteringRecordingsTest(TestCase):
    """Test multi-tag filtering on /events?filter=past."""

    @classmethod
    def setUpTestData(cls):
        Event.objects.create(
            title='Both R', slug='both-r', start_datetime=timezone.make_aware(timezone.datetime(2025, 1, 1, 12, 0)), status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['python', 'workshop'], published=True,
        )
        Event.objects.create(
            title='Python R', slug='python-r', start_datetime=timezone.make_aware(timezone.datetime(2025, 1, 2, 12, 0)), status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['python'], published=True,
        )

    def test_multi_tag_filter(self):
        response = self.client.get('/events?filter=past&tag=python&tag=workshop')
        self.assertContains(response, 'Both R')
        self.assertNotContains(response, 'Python R')


class MultiTagFilteringProjectsTest(TestCase):
    """Test multi-tag filtering on /projects."""

    @classmethod
    def setUpTestData(cls):
        Project.objects.create(
            title='Both P', slug='both-p', date=date(2025, 1, 1),
            tags=['python', 'ai'], published=True,
        )
        Project.objects.create(
            title='Python P', slug='python-p', date=date(2025, 1, 2),
            tags=['python'], published=True,
        )

    def test_multi_tag_filter(self):
        response = self.client.get('/projects?tag=python&tag=ai')
        self.assertContains(response, 'Both P')
        self.assertNotContains(response, 'Python P')


class MultiTagFilteringCoursesTest(TestCase):
    """Test multi-tag filtering on /courses."""

    @classmethod
    def setUpTestData(cls):
        Course.objects.create(
            title='Both C', slug='both-c',
            tags=['python', 'ai'], status='published',
        )
        Course.objects.create(
            title='Python C', slug='python-c',
            tags=['python'], status='published',
        )

    def test_multi_tag_filter(self):
        response = self.client.get('/courses?tag=python&tag=ai')
        self.assertContains(response, 'Both C')
        self.assertNotContains(response, 'Python C')

    def test_single_tag_filter(self):
        response = self.client.get('/courses?tag=python')
        self.assertContains(response, 'Both C')
        self.assertContains(response, 'Python C')

    def test_tag_filtering_works(self):
        response = self.client.get('/courses?tag=python')
        self.assertContains(response, 'Both C')
        self.assertContains(response, 'Python C')


class MultiTagFilteringDownloadsTest(TestCase):
    """Test multi-tag filtering on /downloads."""

    @classmethod
    def setUpTestData(cls):
        Download.objects.create(
            title='Both D', slug='both-d',
            file_url='https://example.com/file.pdf',
            tags=['python', 'ai'], published=True,
        )
        Download.objects.create(
            title='Python D', slug='python-d',
            file_url='https://example.com/file2.pdf',
            tags=['python'], published=True,
        )

    def test_multi_tag_filter(self):
        response = self.client.get('/downloads?tag=python&tag=ai')
        self.assertContains(response, 'Both D')
        self.assertNotContains(response, 'Python D')


class MultiTagFilteringResourcesTest(TestCase):
    """Test multi-tag filtering on /resources."""

    @classmethod
    def setUpTestData(cls):
        CuratedLink.objects.create(
            item_id='both-l', title='Both L',
            url='https://example.com', category='tools',
            tags=['python', 'ai'], published=True,
        )
        CuratedLink.objects.create(
            item_id='python-l', title='Python L',
            url='https://example.com', category='tools',
            tags=['python'], published=True,
        )

    def test_multi_tag_filter(self):
        response = self.client.get('/resources?tag=python&tag=ai')
        self.assertContains(response, 'Both L')
        self.assertNotContains(response, 'Python L')


# --- TagRule Model Tests ---


class TagRuleModelTest(TestCase):
    """Test TagRule model creation and fields."""

    def test_create_tag_rule(self):
        rule = TagRule.objects.create(
            tag='ai-engineer',
            component_type='course_promo',
            component_config={'course_slug': 'python-data-ai', 'cta_text': 'Start learning'},
            position='after_content',
        )
        self.assertEqual(rule.tag, 'ai-engineer')
        self.assertEqual(rule.component_type, 'course_promo')
        self.assertEqual(rule.position, 'after_content')
        self.assertEqual(rule.component_config['course_slug'], 'python-data-ai')
        self.assertIsNotNone(rule.id)

    def test_tag_normalized_on_save(self):
        rule = TagRule.objects.create(
            tag='AI Engineer',
            component_type='course_promo',
            component_config={},
            position='after_content',
        )
        self.assertEqual(rule.tag, 'ai-engineer')

    def test_uuid_primary_key(self):
        rule = TagRule.objects.create(
            tag='test',
            component_type='test',
            component_config={},
            position='after_content',
        )
        import uuid
        self.assertIsInstance(rule.id, uuid.UUID)

    def test_default_position_is_after_content(self):
        rule = TagRule.objects.create(
            tag='test',
            component_type='test',
            component_config={},
        )
        self.assertEqual(rule.position, 'after_content')


# --- TagRule Admin Tests ---


class TagRuleAdminTest(TestCase):
    """Test admin CRUD for TagRules."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_tagrule_list(self):
        TagRule.objects.create(
            tag='python', component_type='course_promo',
            component_config={}, position='after_content',
        )
        response = self.client.get('/admin/content/tagrule/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'python')

    def test_admin_tagrule_add_page(self):
        response = self.client.get('/admin/content/tagrule/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_create_tagrule(self):
        self.client.post('/admin/content/tagrule/add/', {
            'tag': 'ai-engineer',
            'component_type': 'course_promo',
            'component_config': '{"course_slug": "test"}',
            'position': 'after_content',
        })
        self.assertEqual(TagRule.objects.filter(tag='ai-engineer').count(), 1)

    def test_admin_delete_tagrule(self):
        rule = TagRule.objects.create(
            tag='delete-me', component_type='test',
            component_config={}, position='after_content',
        )
        self.client.post(
            f'/admin/content/tagrule/{rule.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(TagRule.objects.filter(tag='delete-me').count(), 0)


# --- TagRule Component Injection Tests ---


class TagRuleInjectionBlogTest(TestCase):
    """Test that TagRules inject components on blog detail pages."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='AI Article', slug='ai-article',
            date=date(2025, 6, 15),
            content_markdown='Some content here.',
            tags=['ai-engineer', 'python'],
            published=True,
        )
        cls.rule = TagRule.objects.create(
            tag='ai-engineer',
            component_type='course_promo',
            component_config={
                'course_slug': 'python-data-ai',
                'cta_text': 'Start learning',
                'title': 'Recommended Course',
            },
            position='after_content',
        )

    def test_tag_rule_component_rendered(self):
        response = self.client.get('/blog/ai-article')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('tag-rule-component', content)
        self.assertIn('Recommended Course', content)
        self.assertIn('Start learning', content)

    def test_tag_rule_in_context(self):
        response = self.client.get('/blog/ai-article')
        tag_rules = response.context['tag_rules']
        self.assertEqual(len(tag_rules['after_content']), 1)
        self.assertEqual(tag_rules['after_content'][0].tag, 'ai-engineer')

    def test_no_tag_rule_when_no_match(self):
        Article.objects.create(
            title='Unmatched', slug='unmatched',
            date=date(2025, 6, 14),
            content_markdown='Content.',
            tags=['golang'],
            published=True,
        )
        response = self.client.get('/blog/unmatched')
        tag_rules = response.context['tag_rules']
        self.assertEqual(len(tag_rules['after_content']), 0)

    def test_tag_rule_not_shown_for_gated_content(self):
        """Gated articles should not show tag rule components (they show the gated CTA instead)."""
        from content.access import LEVEL_BASIC
        Article.objects.create(
            title='Gated AI', slug='gated-ai',
            date=date(2025, 6, 14),
            content_markdown='Secret content.',
            tags=['ai-engineer'],
            required_level=LEVEL_BASIC,
            published=True,
        )
        response = self.client.get('/blog/gated-ai')
        content = response.content.decode()
        # The gated content shows the upgrade CTA, not the tag rule
        self.assertNotIn('tag-rule-component', content)


class TagRuleInjectionProjectTest(TestCase):
    """Test that TagRules inject components on project detail pages."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='AI Project', slug='ai-project',
            date=date(2025, 6, 15),
            content_markdown='Project content.',
            tags=['ai-engineer'],
            published=True,
        )
        TagRule.objects.create(
            tag='ai-engineer',
            component_type='download_cta',
            component_config={
                'title': 'Get the Guide',
                'download_slug': 'ai-guide',
            },
            position='after_content',
        )

    def test_tag_rule_rendered_on_project(self):
        response = self.client.get('/projects/ai-project')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'tag-rule-component')
        self.assertContains(response, 'Get the Guide')


class TagRuleInjectionRecordingTest(TestCase):
    """Issue #426: tag-rule injection no longer runs on event detail pages.

    The event detail page is announcement-only and does not host the
    after-content rule slot. Tag-rule injection on article detail pages
    is still covered by
    ``content/tests/test_seo.py::TagRuleInjectionTest``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.recording = Event.objects.create(
            title='AI Recording', slug='ai-recording',
            start_datetime=timezone.make_aware(timezone.datetime(2025, 6, 15, 12, 0)), status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['ai-engineer'],
            published=True,
        )
        TagRule.objects.create(
            tag='ai-engineer',
            component_type='roadmap_signup',
            component_config={
                'title': 'AI Roadmap',
                'url': '/roadmap',
            },
            position='after_content',
        )

    def test_tag_rule_not_rendered_on_event_detail(self):
        response = self.client.get('/events/ai-recording')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'tag-rule-component')
        self.assertNotContains(response, 'AI Roadmap')


# Tag filter chips were removed from listing pages (tag filtering
# still works via URL params, tested in other test classes above).


# --- Conversions from playwright_tests/test_seo_tags.py (issue #256) ---


class TagIndexPageTest(TestCase):
    """Behaviour previously covered by Playwright Scenarios 1, 9, 10
    on /tags. Asserts on context counts, link href targets, ordering,
    and the empty state — no JS required.
    """

    def test_tag_cloud_shows_tags_with_counts_and_orders_by_count(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario1TagCloudExploration::test_tags_page_shows_tags_with_counts_sorted_by_count
        Article.objects.create(
            title='Python Basics', slug='python-basics',
            date=date(2026, 1, 3), tags=['python', 'ai'], published=True,
        )
        Article.objects.create(
            title='Python Advanced', slug='python-advanced',
            date=date(2026, 1, 2), tags=['python', 'ai'], published=True,
        )
        Event.objects.create(
            title='AI Workshop Recording', slug='ai-workshop-rec',
            start_datetime=timezone.make_aware(timezone.datetime(2026, 1, 1, 12, 0)),
            status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['ai', 'workshop'], published=True,
        )

        response = self.client.get('/tags')
        self.assertEqual(response.status_code, 200)

        # Each tag has a link to its detail page (chip in the cloud).
        self.assertContains(response, 'href="/tags/ai"')
        self.assertContains(response, 'href="/tags/python"')
        self.assertContains(response, 'href="/tags/workshop"')

        # Counts are reflected in the context: ai=3, python=2, workshop=1.
        tag_dict = dict(response.context['tag_counts'])
        self.assertEqual(tag_dict['ai'], 3)
        self.assertEqual(tag_dict['python'], 2)
        self.assertEqual(tag_dict['workshop'], 1)

        # Ordering: highest counts first — "ai" appears before "workshop".
        content = response.content.decode()
        self.assertLess(
            content.index('/tags/ai"'),
            content.index('/tags/workshop"'),
        )

    def test_tag_link_targets_tag_detail_with_all_tagged_items(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario1TagCloudExploration::test_click_tag_navigates_to_tag_detail
        Article.objects.create(
            title='Python Basics', slug='python-basics',
            date=date(2026, 1, 3), tags=['python', 'ai'], published=True,
        )
        Article.objects.create(
            title='Python Advanced', slug='python-advanced',
            date=date(2026, 1, 2), tags=['python', 'ai'], published=True,
        )
        Event.objects.create(
            title='AI Workshop Recording', slug='ai-workshop-rec',
            start_datetime=timezone.make_aware(timezone.datetime(2026, 1, 1, 12, 0)),
            status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['ai', 'workshop'], published=True,
        )

        # The tag chip on /tags links to /tags/ai (the "click" target).
        index = self.client.get('/tags')
        self.assertContains(index, 'href="/tags/ai"')

        # Following that link reaches the tag detail page with all 3
        # items and both content type badges.
        detail = self.client.get('/tags/ai')
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Python Basics')
        self.assertContains(detail, 'Python Advanced')
        self.assertContains(detail, 'AI Workshop Recording')
        self.assertContains(detail, 'Article')
        # The unified Event model labels recordings as "Event" badges
        # (see TagsDetailViewTest.test_shows_content_type_badges above).
        self.assertContains(detail, 'Event')

    def test_empty_tags_page_shows_message(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario9EmptyTagsPage::test_empty_tags_page_shows_message
        # No content created — page should still render with empty state.
        response = self.client.get('/tags')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No tags yet')

        # The header (with navigation) is still present so the visitor
        # can navigate elsewhere.
        self.assertContains(response, '<header')
        self.assertContains(response, '<nav')


class TagDetailPageTest(TestCase):
    """Behaviour previously covered by Playwright Scenarios 2 and 10
    on /tags/{tag}. Asserts on result ordering, item link targets,
    and the "All Tags" back-link.
    """

    def test_tag_detail_shows_both_items_sorted_newest_first(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario2DrillIntoTagAndNavigate::test_tag_detail_shows_both_items_sorted_by_date
        Article.objects.create(
            title='Intro to AI Engineering',
            slug='intro-to-ai-engineering',
            description='An introduction to AI engineering.',
            content_markdown='# Intro to AI Engineering',
            date=date(2026, 2, 15),
            tags=['ai-engineering'], published=True,
        )
        Event.objects.create(
            title='AI Workshop', slug='ai-workshop',
            description='A workshop on AI engineering.',
            start_datetime=timezone.make_aware(timezone.datetime(2026, 2, 10, 12, 0)),
            status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['ai-engineering'], published=True,
        )

        response = self.client.get('/tags/ai-engineering')
        self.assertEqual(response.status_code, 200)

        # Both items present, badges visible.
        self.assertContains(response, 'Intro to AI Engineering')
        self.assertContains(response, 'AI Workshop')
        self.assertContains(response, 'Article')
        self.assertContains(response, 'Event')

        # Ordering: results context is sorted newest first
        # (article on Feb 15 before recording on Feb 10).
        titles = [r['title'] for r in response.context['results']]
        self.assertEqual(
            titles.index('Intro to AI Engineering'),
            titles.index('AI Workshop') - 1,
        )

    def test_article_link_from_tag_detail_reaches_article_detail(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario2DrillIntoTagAndNavigate::test_click_article_from_tag_detail
        Article.objects.create(
            title='Intro to AI Engineering',
            slug='intro-to-ai-engineering',
            description='An introduction to AI engineering.',
            content_markdown=(
                '# Intro to AI Engineering\n\nFull article content about AI.'
            ),
            date=date(2026, 2, 15),
            tags=['ai-engineering'], published=True,
        )
        Event.objects.create(
            title='AI Workshop', slug='ai-workshop',
            start_datetime=timezone.make_aware(timezone.datetime(2026, 2, 10, 12, 0)),
            status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['ai-engineering'], published=True,
        )

        # The tag detail page links the article title to /blog/<slug>.
        tag_page = self.client.get('/tags/ai-engineering')
        self.assertContains(tag_page, 'href="/blog/intro-to-ai-engineering"')

        # That target resolves and shows the rendered article body.
        article_page = self.client.get('/blog/intro-to-ai-engineering')
        self.assertEqual(article_page.status_code, 200)
        self.assertContains(article_page, 'Intro to AI Engineering')
        self.assertContains(article_page, 'Full article content about AI')

    def test_navigate_tag_index_to_detail_and_back_to_other_tag(self):
        # Replaces playwright_tests/test_seo_tags.py::TestScenario10NavigateBetweenTagPages::test_navigate_tag_index_to_detail_and_back
        Article.objects.create(
            title='Python Tutorial', slug='python-tutorial',
            date=date(2026, 1, 3), tags=['python'], published=True,
        )
        Article.objects.create(
            title='AI Guide', slug='ai-guide',
            date=date(2026, 1, 2), tags=['ai'], published=True,
        )
        Event.objects.create(
            title='Python Workshop', slug='python-workshop',
            start_datetime=timezone.make_aware(timezone.datetime(2026, 1, 1, 12, 0)),
            status='completed', recording_url='https://youtube.com/watch?v=test',
            tags=['python'], published=True,
        )

        # Step 1: /tags lists every tag chip.
        index = self.client.get('/tags')
        self.assertContains(index, 'href="/tags/python"')
        self.assertContains(index, 'href="/tags/ai"')

        # Step 2: /tags/python shows both python-tagged items.
        python_page = self.client.get('/tags/python')
        self.assertEqual(python_page.status_code, 200)
        self.assertContains(python_page, 'Python Tutorial')
        self.assertContains(python_page, 'Python Workshop')
        # And links back to the index via "All Tags".
        self.assertContains(python_page, 'href="/tags"')
        self.assertContains(python_page, 'All Tags')

        # Step 3: /tags/ai shows only ai-tagged content (no python items).
        ai_page = self.client.get('/tags/ai')
        self.assertEqual(ai_page.status_code, 200)
        self.assertContains(ai_page, 'AI Guide')
        self.assertNotContains(ai_page, 'Python Tutorial')
        self.assertNotContains(ai_page, 'Python Workshop')
