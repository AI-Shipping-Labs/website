from django.template.loader import render_to_string
from django.test import SimpleTestCase

from content.services.project_criteria import criteria_summary

CRITERIA = [
    {
        'id': 'problem', 'title': 'Problem description', 'description': '', 'type': 'scale',
        'options': [
            {'points': 0, 'text': 'Not described'},
            {'points': 1, 'text': 'Briefly described'},
            {'points': 2, 'text': 'Clearly described'},
        ],
    },
    {
        'id': 'practices', 'title': 'Best practices', 'description': '', 'type': 'checklist',
        'options': [
            {'points': 1, 'text': 'Docker'},
            {'points': 2, 'text': 'CI/CD'},
        ],
    },
]


class CriteriaSummaryTest(SimpleTestCase):
    def setUp(self):
        self.summary = criteria_summary(CRITERIA)

    def test_keeps_authored_order_and_totals(self):
        self.assertEqual([c['id'] for c in self.summary['criteria']], ['problem', 'practices'])
        self.assertEqual(self.summary['core_max'], 2)
        self.assertEqual(self.summary['bonus_max'], 3)
        self.assertEqual(self.summary['totals_label'], 'up to 2 points, plus up to 3 bonus points')

    def test_totals_label_omits_bonus_part_without_bonus_criteria(self):
        self.assertEqual(criteria_summary(CRITERIA[:1])['totals_label'], 'up to 2 points')

    def test_scale_lines_read_like_the_lesson_with_pluralized_points(self):
        row = self.summary['criteria'][0]
        self.assertEqual(row['list_intro'], 'Scoring:')
        self.assertEqual(
            [o['label'] for o in row['options']],
            ['Not described: 0 points', 'Briefly described: 1 point', 'Clearly described: 2 points'],
        )

    def test_checklist_rows_use_bonus_intro(self):
        row = self.summary['criteria'][1]
        self.assertEqual(row['list_intro'], 'Bonus points (checklist):')
        self.assertEqual([o['label'] for o in row['options']], ['Docker: 1 point', 'CI/CD: 2 points'])


class CriteriaPartialTest(SimpleTestCase):
    def test_renders_heading_intro_and_option_lines(self):
        html = render_to_string('content/peer_review/_criteria.html', {
            'project_criteria': criteria_summary(CRITERIA),
            'criteria_heading_level': 3,
            'criteria_id_prefix': 'criterion-',
        })
        self.assertInHTML('<h3 id="criterion-problem">Problem description</h3>', html)
        self.assertInHTML('<p>Bonus points (checklist):</p>', html)
        self.assertInHTML('<li>Briefly described: 1 point</li>', html)

    def test_defaults_to_h2_with_bare_criterion_ids(self):
        html = render_to_string('content/peer_review/_criteria.html', {
            'project_criteria': criteria_summary(CRITERIA),
        })
        self.assertInHTML('<h2 id="practices">Best practices</h2>', html)
