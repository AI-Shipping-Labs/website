"""CMP project scoring (content.services.project_results) and peer pseudonyms."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from content.services.peer_pseudonym import peer_pseudonym
from content.services.project_results import project_result

CRITERIA = [
    {'id': 'readme', 'title': 'README', 'description': '', 'type': 'scale', 'options': [
        {'points': 0, 'text': 'None'}, {'points': 1, 'text': 'Brief'}, {'points': 2, 'text': 'Clear'},
    ]},
    {'id': 'extras', 'title': 'Extras', 'description': '', 'type': 'checklist', 'options': [
        {'points': 1, 'text': 'UI'}, {'points': 2, 'text': 'Deployed'},
    ]},
]


def review(scores):
    return SimpleNamespace(criteria_scores=scores)


class ProjectResultTest(SimpleTestCase):
    def test_median_per_criterion_rounds_up_and_sums(self):
        result = project_result(CRITERIA, [
            review({'readme': 1, 'extras': [0, 1]}),
            review({'readme': 2, 'extras': []}),
        ])
        self.assertEqual([row.score for row in result.criteria], [2, 2])
        self.assertEqual(result.total, 4)

    def test_votes_count_each_chosen_option(self):
        result = project_result(CRITERIA, [
            review({'readme': 2, 'extras': [1]}),
            review({'readme': 2, 'extras': [0, 1]}),
            review({'readme': 0, 'extras': []}),
        ])
        readme, extras = result.criteria
        self.assertEqual([opt['votes'] for opt in readme.options], [1, 0, 2])
        self.assertEqual([opt['votes'] for opt in extras.options], [1, 2])
        self.assertEqual(readme.options[2]['label'], 'Clear (2 points)')

    def test_blank_or_invalid_answers_score_zero(self):
        result = project_result(CRITERIA, [review({}), review({'readme': 9, 'extras': 'x'})])
        self.assertEqual(result.total, 0)

    def test_no_reviews_falls_back_to_cmp_default_scores(self):
        result = project_result(CRITERIA, [])
        self.assertEqual([row.score for row in result.criteria], [1, 2])
        self.assertEqual(result.review_count, 0)

    def test_chosen_option_names_the_median(self):
        result = project_result(CRITERIA, [
            review({'readme': 2, 'extras': [0, 1]}),
            review({'readme': 2, 'extras': [1]}),
            review({'readme': 0, 'extras': [1]}),
        ])
        readme, extras = result.criteria
        self.assertEqual(readme.chosen_text, 'Clear')
        self.assertEqual([opt['chosen'] for opt in readme.options], [False, False, True])
        self.assertEqual(extras.chosen_text, 'Deployed')
        self.assertEqual((readme.max_points, extras.max_points), (2, 3))

    def test_median_between_options_names_none(self):
        result = project_result(CRITERIA, [
            review({'readme': 0, 'extras': [0, 1]}),
            review({'readme': 2, 'extras': []}),
        ])
        readme, extras = result.criteria
        self.assertEqual(readme.chosen_text, 'Brief')
        self.assertEqual(extras.chosen_text, '')

    def test_defaults_name_no_option(self):
        result = project_result(CRITERIA, [])
        self.assertEqual([row.chosen_text for row in result.criteria], ['', ''])

    def test_rows_keep_authored_order_with_bonus_flag(self):
        criteria = [CRITERIA[1], CRITERIA[0]]
        result = project_result(criteria, [review({'readme': 1, 'extras': [1]})])
        self.assertEqual([(row.id, row.is_bonus) for row in result.criteria], [('extras', True), ('readme', False)])
        self.assertEqual([row.bonus_marker for row in result.criteria], [True, False])

    def test_no_criteria_has_no_result(self):
        self.assertIsNone(project_result([], [review({})]))

    def test_passed_needs_score_and_completed_reviews(self):
        reviews = [review({'readme': 2, 'extras': [0, 1]})]
        self.assertTrue(project_result(CRITERIA, reviews, 5, True).passed)
        self.assertFalse(project_result(CRITERIA, reviews, 5, False).passed)
        self.assertFalse(project_result(CRITERIA, reviews, 6, True).passed)
        self.assertIsNone(project_result(CRITERIA, reviews, 0, True).passed)


class PeerPseudonymTest(SimpleTestCase):
    def test_stable_per_learner_and_course(self):
        name = peer_pseudonym(7, 3)
        self.assertEqual(name, peer_pseudonym(7, 3))
        self.assertEqual(len(name.split(' ')), 2)
        others = {peer_pseudonym(user_id, 3) for user_id in range(1, 40)}
        self.assertGreater(len(others), 30)
