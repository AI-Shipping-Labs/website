"""Data integrity and link rot guard for ``backgrounds.yaml`` (issue #1716)."""

import copy

from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone

from content.models import Course, Project, Workshop
from skill_gap.backgrounds import (
    BackgroundDataError,
    _validate_background,
    all_backgrounds,
)

# The five field-guide learning paths (learning-paths/from-*.md).
FIELD_GUIDE_KEYS = (
    'data-engineer',
    'data-scientist',
    'ml-engineer',
    'backend-engineer',
    'frontend-engineer',
)

# URL prefix -> (model, create kwargs, detail template).
DETAIL_FIXTURES = {
    'courses': (Course, {'status': 'published'}, 'content/course_detail.html'),
    'workshops': (Workshop, {'status': 'published'}, 'content/workshop_detail.html'),
    'projects': (Project, {'published': True}, 'content/project_detail.html'),
}
INDEX_TEMPLATES = {
    '/sprints': 'content/sprints_index.html',
}


def _all_next_actions():
    """Every distinct next action in the data file, keyed by URL."""
    actions = {}
    for profile in all_backgrounds().values():
        for action in profile['next_actions']:
            actions[action['url']] = action
    return actions


class BackgroundDataTest(SimpleTestCase):
    def test_file_maps_exactly_the_five_field_guide_backgrounds(self):
        self.assertEqual(tuple(all_backgrounds()), FIELD_GUIDE_KEYS)

    def test_every_background_has_three_next_actions_with_a_why(self):
        for key, profile in all_backgrounds().items():
            with self.subTest(background=key):
                actions = profile['next_actions']
                self.assertEqual(len(actions), 3)
                self.assertTrue(all(action['why'].strip() for action in actions))

    def test_validator_rejects_a_background_without_three_actions(self):
        profile = copy.deepcopy(all_backgrounds()['data-engineer'])
        profile['next_actions'] = profile['next_actions'][:2]

        with self.assertRaisesMessage(BackgroundDataError, 'exactly 3 next_actions'):
            _validate_background('data-engineer', profile)

    def test_validator_rejects_an_external_or_blank_action(self):
        base = all_backgrounds()['data-engineer']
        for field, value in (('url', 'https://example.com/x'), ('url', '//evil.example'), ('why', ' ')):
            with self.subTest(field=field, value=value):
                profile = copy.deepcopy(base)
                profile['next_actions'][0][field] = value
                with self.assertRaises(BackgroundDataError):
                    _validate_background('data-engineer', profile)


@tag('core')
class NextActionUrlsResolveTest(TestCase):
    """GET every next-action URL against the real views.

    Fixture rows are created from the data file itself (same slug and
    title), so a renamed route, a changed slug shape, or a broken detail
    view fails here instead of shipping a dead link.
    """

    @classmethod
    def setUpTestData(cls):
        for url, action in _all_next_actions().items():
            parts = [segment for segment in url.split('/') if segment]
            if url in INDEX_TEMPLATES:
                continue
            model, extra, _ = DETAIL_FIXTURES[parts[0]]
            kwargs = {'title': action['title'], 'slug': parts[1], **extra}
            if model in (Workshop, Project):
                kwargs['date'] = timezone.now().date()
            model.objects.create(**kwargs)

    def test_every_next_action_url_renders_its_asset(self):
        for url, action in _all_next_actions().items():
            with self.subTest(url=url):
                response = self.client.get(url)
                if url in INDEX_TEMPLATES:
                    self.assertTemplateUsed(response, INDEX_TEMPLATES[url])
                    self.assertContains(response, 'data-testid="sprints-index-page"')
                    continue
                parts = [segment for segment in url.split('/') if segment]
                self.assertEqual(len(parts), 2, url)
                self.assertTemplateUsed(response, DETAIL_FIXTURES[parts[0]][2])
                self.assertContains(response, action['title'])
