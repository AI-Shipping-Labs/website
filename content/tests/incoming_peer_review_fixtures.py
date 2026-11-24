"""Shared local-only fixtures for incoming peer review behavior tests."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from content.models import Course, CourseProject, PeerReview, ProjectSubmission
from content.models.peer_review import ProjectState

CRITERIA = [
    {'id': 'quality', 'title': 'Project quality', 'type': 'scale', 'options': [
        {'points': 0, 'text': 'Missing'}, {'points': 3, 'text': 'Complete'},
    ]},
    {'id': 'extras', 'title': 'Bonus work', 'type': 'checklist', 'options': [
        {'points': 1, 'text': 'Tests'}, {'points': 1, 'text': 'Deployment'},
    ]},
]


class IncomingReviewFixtures:
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.owner = user_model.objects.create_user(
            email='george@example.com', first_name='George Cosmin', last_name='Taschina',
        )
        cls.staff = user_model.objects.create_user(email='staff@example.com', is_staff=True)
        cls.reviewer = user_model.objects.create_user(email='reviewer@example.com')
        cls.second = user_model.objects.create_user(email='second@example.com')
        cls.pending = user_model.objects.create_user(email='pending@example.com')
        cls.course = Course.objects.create(
            slug='ai-buildcamp', title='AI Buildcamp', status='published',
            peer_review_enabled=True, project_passing_score=3,
        )
        cls.project = CourseProject.objects.create(
            course=cls.course, slug='attempt-1', title='Capstone Project Attempt 1',
            evaluation_criteria=CRITERIA, state=ProjectState.PEER_REVIEWING,
            submission_due_at=timezone.now() - timedelta(days=2),
            review_due_at=timezone.now() + timedelta(days=2),
        )
        cls.submission = ProjectSubmission.objects.create(
            course=cls.course, course_project=cls.project, user=cls.owner,
            project_url='https://github.com/george/project', status='in_review',
        )
        cls._create_reviews()

    @classmethod
    def _create_reviews(cls):
        cls.first_review = PeerReview.objects.create(
            submission=cls.submission, reviewer=cls.reviewer, is_complete=True,
            criteria_scores={'quality': 0, 'extras': [0, 1]}, feedback='First useful feedback',
            completed_at=timezone.now(),
        )
        cls.second_review = PeerReview.objects.create(
            submission=cls.submission, reviewer=cls.second, is_complete=True,
            criteria_scores={'quality': 1, 'extras': []}, feedback='Second useful feedback',
            completed_at=timezone.now(),
        )
        cls.pending_review = PeerReview.objects.create(
            submission=cls.submission, reviewer=cls.pending,
            criteria_scores={'quality': 1, 'extras': [0, 1]}, feedback='Private draft',
        )
