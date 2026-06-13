"""Tests for the Studio user-detail Activity section (issue #853)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import UserActivity

User = get_user_model()


class ActivitySectionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-853@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-853@test.com', password='pw',
        )
        # Creating users fires the signup signal; clear so each test
        # controls the rows it expects.
        UserActivity.objects.all().delete()

    def setUp(self):
        self.client.login(email='staff-853@test.com', password='pw')

    def _url(self):
        return reverse('studio_user_detail', args=[self.member.pk])

    def _add(self, event_type, label, target_url='', minutes_ago=0):
        return UserActivity.objects.create(
            user=self.member,
            event_type=event_type,
            label=label,
            target_url=target_url,
            occurred_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
        )

    def test_section_renders_with_heading(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="user-activity-section"')
        self.assertContains(response, '>Activity<')

    def test_empty_state(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="user-activity-empty"')
        self.assertContains(response, 'No recorded activity yet.')

    def test_rows_render_newest_first(self):
        self._add(UserActivity.EVENT_SIGNUP, 'Signed up', minutes_ago=30)
        self._add(
            UserActivity.EVENT_COURSE_ENROLL,
            'Enrolled in course: LLM Zoomcamp',
            minutes_ago=10,
        )
        response = self.client.get(self._url())
        content = response.content.decode()

        self.assertContains(response, 'data-testid="user-activity-row"', count=2)
        self.assertContains(response, 'Enrolled in course: LLM Zoomcamp')
        self.assertContains(response, 'Signed up')
        # Newest (enroll, 10 min ago) appears before oldest (signup).
        self.assertLess(
            content.index('Enrolled in course: LLM Zoomcamp'),
            content.index('Signed up'),
        )

    def test_type_label_rendered(self):
        self._add(UserActivity.EVENT_EMAIL_CLICK, 'Clicked email link')
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="user-activity-type"')
        self.assertContains(response, 'Email click')

    def test_target_url_makes_label_clickable(self):
        self._add(
            UserActivity.EVENT_COURSE_ENROLL,
            'Enrolled in course: DE',
            target_url='/studio/courses/5/edit',
        )
        response = self.client.get(self._url())
        self.assertContains(response, 'href="/studio/courses/5/edit"')

    def test_more_line_when_over_limit(self):
        # Display window raised to 30 in #773 (resource_view rows share
        # the timeline).
        for i in range(35):
            self._add(UserActivity.EVENT_LESSON_OPEN, f'Lesson {i}', minutes_ago=i)
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="user-activity-row"', count=30)
        self.assertContains(response, 'data-testid="user-activity-more"')
        self.assertContains(response, 'Showing 30 of 35 events')

    def test_no_more_line_when_at_or_under_limit(self):
        for i in range(30):
            self._add(UserActivity.EVENT_LESSON_OPEN, f'Lesson {i}', minutes_ago=i)
        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="user-activity-row"', count=30)
        self.assertNotContains(response, 'data-testid="user-activity-more"')

    def test_query_budget_is_bounded(self):
        # Many events must not add per-row queries. The activity section
        # adds at most 2 queries (window + count). Measure the delta
        # between an empty user and a busy user.
        busy = self.member
        for i in range(40):
            self._add(UserActivity.EVENT_LESSON_OPEN, f'Lesson {i}', minutes_ago=i)

        from studio.views.users import _build_activity_timeline

        with self.assertNumQueries(2):
            _build_activity_timeline(busy)

    def test_query_budget_empty_user_single_query(self):
        from studio.views.users import _build_activity_timeline

        # Under the window -> total derived from the window, no count query.
        with self.assertNumQueries(1):
            _build_activity_timeline(self.member)

    def test_non_staff_denied(self):
        self.client.logout()
        self.client.login(email='member-853@test.com', password='pw')
        response = self.client.get(self._url())
        self.assertIn(response.status_code, (302, 403))
