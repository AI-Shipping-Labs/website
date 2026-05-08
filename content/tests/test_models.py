"""Lookup-table tests for ``Project`` and ``CuratedLink``.

Each test below exercises a single ``match``/``dict`` lookup and is
written as one ``subTest``-parameterized case rather than one test
method per branch (issue #533, ``_docs/testing-guidelines.md`` Rule
on per-enum tests).
"""

from django.test import TestCase, tag

from content.models import CuratedLink, Project


@tag('core')
class ProjectDifficultyColorTest(TestCase):
    """``Project.difficulty_color`` returns a Tailwind class for each
    difficulty level. The mapping is a 4-row dict lookup."""

    def test_difficulty_color_table(self):
        cases = [
            ('intermediate', 'bg-yellow-500/20 text-yellow-400'),
            ('beginner', 'bg-green-500/20 text-green-400'),
            ('advanced', 'bg-red-500/20 text-red-400'),
            ('', 'bg-secondary text-muted-foreground'),
        ]
        project = Project(slug='difficulty-test')
        for difficulty, expected_class in cases:
            with self.subTest(difficulty=difficulty):
                project.difficulty = difficulty
                self.assertEqual(project.difficulty_color(), expected_class)


@tag('core')
class CuratedLinkCategoryLookupTest(TestCase):
    """``CuratedLink.category_label`` and ``CuratedLink.category_icon_name``
    are pure dict lookups on ``category``. One ``subTest`` row per
    category covers both lookups together — every shipping category is
    in the table, so adding a new one is one row, not two test
    methods."""

    def test_category_label_and_icon(self):
        cases = [
            # (category, expected_label, expected_icon)
            ('tools', 'Tools', 'wrench'),
            ('models', 'Models', 'cpu'),
            # Issue #524: ``courses`` icon is ``book-open`` (was
            # ``graduation-cap``) so it doesn't collide with the new
            # ``workshops`` category.
            ('courses', 'Courses', 'book-open'),
            ('workshops', 'Workshops', 'graduation-cap'),
            ('articles', 'Articles', 'file-text'),
            ('other', 'Other', 'folder-open'),
        ]
        link = CuratedLink(item_id='cat', title='cat', url='https://x.test')
        for category, expected_label, expected_icon in cases:
            with self.subTest(category=category):
                link.category = category
                self.assertEqual(link.category_label, expected_label)
                self.assertEqual(link.category_icon_name, expected_icon)


@tag('core')
class CuratedLinkIsExternalTest(TestCase):
    """``CuratedLink.is_external`` returns True for absolute URLs and
    False for path-relative ones. Both branches in one test."""

    def test_is_external_branches(self):
        cases = [
            ('https://example.com/test', True),
            ('http://example.com/test', True),
            ('/internal/path', False),
        ]
        link = CuratedLink(item_id='ext', title='ext', category='tools')
        for url, expected in cases:
            with self.subTest(url=url):
                link.url = url
                self.assertEqual(link.is_external, expected)
