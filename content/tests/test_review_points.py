"""Per-review criteria points on staff surfaces (admin list, coursework inventory)."""

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from content.admin.peer_review import PeerReviewAdmin
from content.models import Course, CourseProject, PeerReview, ProjectSubmission
from content.services.coursework_inventory import build_coursework_inventory
from content.services.project_results import review_points

User = get_user_model()

CRITERIA = [
    {'id': 'readme', 'title': 'README', 'description': '', 'type': 'scale', 'options': [
        {'points': 0, 'text': 'None'}, {'points': 1, 'text': 'Brief'}, {'points': 5, 'text': 'Clear'},
    ]},
    {'id': 'extras', 'title': 'Extras', 'description': '', 'type': 'checklist', 'options': [
        {'points': 3, 'text': 'UI'}, {'points': 6, 'text': 'Deployed'},
    ]},
]


class ReviewPointsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = User.objects.create_user(email='author@test.com')
        reviewer = User.objects.create_user(email='rev@test.com')
        cls.course = Course.objects.create(title='C', slug='c', status='published')
        cls.project = CourseProject.objects.create(
            course=cls.course, slug='p1', title='P1', evaluation_criteria=CRITERIA,
        )
        legacy_project = CourseProject.objects.create(
            course=cls.course, slug='p2', title='P2',
        )
        sub = ProjectSubmission.objects.create(
            user=author, course=cls.course, course_project=cls.project,
            project_url='https://github.com/a/b',
        )
        legacy_sub = ProjectSubmission.objects.create(
            user=reviewer, course=cls.course, course_project=legacy_project,
            project_url='https://github.com/a/c',
        )
        cls.criteria_review = PeerReview.objects.create(
            submission=sub, reviewer=reviewer, is_complete=True, score=None,
            criteria_scores={'readme': 2, 'extras': [0, 1]},
        )
        cls.legacy_review = PeerReview.objects.create(
            submission=legacy_sub, reviewer=author, is_complete=True, score=4,
        )

    def test_review_points_sums_chosen_options(self):
        self.assertEqual(review_points(self.criteria_review), 14)

    def test_review_points_none_for_legacy_review(self):
        self.assertIsNone(review_points(self.legacy_review))

    def test_admin_column_shows_points_for_criteria_review(self):
        model_admin = PeerReviewAdmin(PeerReview, site)
        self.assertEqual(model_admin.criteria_total(self.criteria_review), '14 pts')
        self.assertEqual(model_admin.criteria_total(self.legacy_review), '-')

    def test_admin_changelist_renders_points(self):
        request = RequestFactory().get('/')
        request.user = User.objects.create_superuser(email='su@test.com', password='x')
        request.session = {}
        response = PeerReviewAdmin(PeerReview, site).changelist_view(request)
        response.render()
        self.assertContains(response, '14 pts')

    def test_inventory_counts_criteria_reviews_separately(self):
        reviews = build_coursework_inventory(self.course)['reviews']
        self.assertEqual(reviews['scored'], 1)
        self.assertEqual(reviews['criteria_scored'], 1)
        self.assertEqual(reviews['unscored'], 0)
