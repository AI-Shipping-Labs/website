"""Tests for the studio peer-review management page (issue #493).

Covers:
- Course-context header (title, peer-review enabled state, settings, back link).
- Cohort grouping with a `Self-paced / no cohort` bucket.
- Friendly action labels (`Create review assignments`, etc.).
- Friendly status filter labels with counts.
- Action endpoints still work and emit success/info messages.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, ProjectSubmission

User = get_user_model()


@tag('core')
class PeerReviewManagementHeaderTest(TestCase):
    """The course-context header must identify the course and configuration."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Shipping AI 101',
            slug='shipping-ai-101',
            status='published',
            peer_review_enabled=True,
            peer_review_count=2,
            peer_review_deadline_days=10,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_returns_200(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertTemplateUsed(response, 'studio/courses/peer_reviews.html')

    def test_header_shows_course_title(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'Peer reviews for Shipping AI 101')

    def test_header_shows_peer_review_enabled_state(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="peer-review-status"')
        self.assertContains(response, 'Peer review enabled')

    def test_header_shows_review_count_setting(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="peer-review-count"')
        # Count = 2 from the fixture.
        self.assertContains(response, '>2<')

    def test_header_shows_review_deadline_days(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="peer-review-deadline-days"')
        self.assertContains(response, '10 days')

    def test_header_links_back_to_course_edit(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="back-to-course-edit"')
        self.assertContains(response, f'/studio/courses/{self.course.pk}/edit')

    def test_no_template_comment_leak(self):
        """Multi-line `{# #}` blocks leak into HTML; guard against it."""
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        body = response.content.decode()
        # Internal author comments should not be visible in the rendered page.
        self.assertNotIn('Breadcrumbs above the course-context header', body)
        self.assertNotIn('Cohort groups', body)
        self.assertNotIn('Status filter chips', body)
        self.assertNotIn('Action bar with task-oriented copy', body)
        self.assertNotIn('Empty state when peer review is disabled', body)


@tag('core')
class PeerReviewManagementDisabledStateTest(TestCase):
    """When peer review is disabled the page surfaces a clear empty state."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Inactive Course', slug='inactive-course',
            status='published', peer_review_enabled=False,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_disabled_state_message_renders(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="peer-review-disabled-empty"')
        self.assertContains(response, 'Peer review is turned off for this course')

    def test_status_chip_says_disabled(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'Peer review disabled')


@tag('core')
class PeerReviewCohortGroupingTest(TestCase):
    """Submissions group by cohort, with a `Self-paced / no cohort` bucket."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Cohort Course', slug='cohort-course',
            status='published', peer_review_enabled=True,
        )
        today = timezone.now().date()
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name='March 2026 Cohort',
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
            is_active=True,
        )
        # An enrolled member to make `enrollment_count` > 0.
        cls.enrolled_user = User.objects.create_user(
            email='enrolled@test.com', password='pass',
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.enrolled_user)
        # Cohort-bound submission.
        cls.cohort_submission = ProjectSubmission.objects.create(
            user=cls.enrolled_user,
            course=cls.course,
            cohort=cls.cohort,
            project_url='https://example.com/cohort-project',
            status='submitted',
        )
        # Self-paced submission (no cohort).
        cls.self_paced_user = User.objects.create_user(
            email='solo@test.com', password='pass',
        )
        cls.self_paced_submission = ProjectSubmission.objects.create(
            user=cls.self_paced_user,
            course=cls.course,
            cohort=None,
            project_url='https://example.com/solo-project',
            status='in_review',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_cohort_group_renders_with_name_and_window(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'March 2026 Cohort')
        self.assertContains(response, 'data-testid="cohort-window"')

    def test_cohort_group_shows_active_state(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="cohort-active-state"')
        self.assertContains(response, '>Active<')

    def test_cohort_group_shows_enrollment_count(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'data-testid="cohort-enrollment-count"')
        self.assertContains(response, '1 enrolled')

    def test_self_paced_group_renders(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'Self-paced / no cohort')
        self.assertContains(response, 'data-testid="cohort-self-paced-tag"')

    def test_cohort_submission_appears_in_cohort_group(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        # Both submission rows render somewhere on the page.
        self.assertContains(response, 'enrolled@test.com')
        self.assertContains(response, 'solo@test.com')

    def test_groups_in_context_separate_cohort_and_self_paced(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        cohort_groups = response.context['cohort_groups']
        # Cohort first (because we emit cohorts before the self-paced bucket),
        # then the self-paced bucket.
        self.assertEqual(len(cohort_groups), 2)
        cohort_group, self_paced_group = cohort_groups
        self.assertFalse(cohort_group['is_self_paced'])
        self.assertTrue(self_paced_group['is_self_paced'])
        cohort_emails = [
            i['submission'].user.email for i in cohort_group['items']
        ]
        self_paced_emails = [
            i['submission'].user.email for i in self_paced_group['items']
        ]
        self.assertEqual(cohort_emails, ['enrolled@test.com'])
        self.assertEqual(self_paced_emails, ['solo@test.com'])

    def test_no_submissions_empty_state_when_no_cohorts_no_subs(self):
        empty_course = Course.objects.create(
            title='Empty', slug='empty', status='published',
            peer_review_enabled=True,
        )
        response = self.client.get(
            f'/studio/courses/{empty_course.pk}/peer-reviews',
        )
        # The self-paced bucket renders its own empty message because the
        # course has neither cohorts nor submissions.
        self.assertContains(response, 'data-testid="cohort-group-empty"')


@tag('core')
class PeerReviewActionLabelsTest(TestCase):
    """Action buttons must use task-oriented copy."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Course', slug='course', status='published',
            peer_review_enabled=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_review_assignments_button_label(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'Create review assignments')

    def test_form_batch_label_no_longer_used(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        # The old internal-jargon label must not appear as a button.
        self.assertNotContains(response, '>Form Batch<')

    def test_issue_certificates_uses_eligible_completions_phrase(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(
            response, 'Issue certificates for eligible completions',
        )

    def test_extend_deadline_label(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'Extend review deadline')


@tag('core')
class PeerReviewStatusFiltersTest(TestCase):
    """Status filter chips must use friendly labels and show counts."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Filtered Course', slug='filtered',
            status='published', peer_review_enabled=True,
        )
        # Two submissions with status 'submitted', one 'in_review'.
        for i in range(2):
            user = User.objects.create_user(
                email=f'sub{i}@test.com', password='pass',
            )
            ProjectSubmission.objects.create(
                user=user, course=cls.course,
                project_url=f'https://example.com/{i}',
                status='submitted',
            )
        rev_user = User.objects.create_user(
            email='inreview@test.com', password='pass',
        )
        ProjectSubmission.objects.create(
            user=rev_user, course=cls.course,
            project_url='https://example.com/review',
            status='in_review',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_friendly_labels_appear(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        self.assertContains(response, 'Awaiting reviewers')
        self.assertContains(response, 'Being reviewed')
        self.assertContains(response, 'Reviews complete')
        self.assertContains(response, 'Certificate issued')

    def test_raw_status_codes_not_used_as_filter_labels(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        # The raw codes are still used as data-testids and query-string
        # values, but should not be the visible chip labels.
        self.assertNotContains(response, '>submitted<')
        self.assertNotContains(response, '>in_review<')
        self.assertNotContains(response, '>review_complete<')
        self.assertNotContains(response, '>certified<')

    def test_status_filter_chip_counts(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        # Two submitted, one in_review, total 3.
        # Counts render in parens via `({{ f.count }})`.
        self.assertContains(response, 'data-testid="status-filter-submitted"')
        self.assertContains(response, '(2)')
        self.assertContains(response, '(1)')
        self.assertContains(response, 'data-testid="status-filter-all"')
        self.assertContains(response, '(3)')

    def test_filter_query_param_filters_submissions(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews?status=in_review',
        )
        # Cohort groups still render but only the in_review submission shows up.
        groups = response.context['cohort_groups']
        all_emails = [
            item['submission'].user.email
            for g in groups for item in g['items']
        ]
        self.assertEqual(all_emails, ['inreview@test.com'])

    def test_friendly_status_label_used_on_submission_chip(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews',
        )
        # The chip on each submission row uses the same friendly label.
        self.assertContains(response, 'data-testid="submission-status-label"')
        # Ensure the operator-friendly phrasing wins over the internal code.
        self.assertContains(response, 'Awaiting reviewers')


@tag('core')
class PeerReviewActionEndpointsTest(TestCase):
    """The form-batch / certificate / extend-deadline endpoints still work."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Action Course', slug='action-course',
            status='published', peer_review_enabled=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_form_batch_no_op_uses_friendly_message(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/form-batch',
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No submissions ready for review assignments')

    def test_form_batch_when_disabled_shows_error(self):
        self.course.peer_review_enabled = False
        self.course.save(update_fields=['peer_review_enabled'])
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/form-batch',
            follow=True,
        )
        self.assertContains(response, 'Peer review is not enabled for this course.')

    def test_issue_certificates_no_op_message(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/issue-certificates',
            follow=True,
        )
        self.assertContains(response, 'No eligible completions for certificate issuance')

    def test_extend_deadline_no_op_message(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/extend-deadline',
            {'days': 5}, follow=True,
        )
        self.assertContains(response, 'No in-review submissions to extend')

    def test_extend_deadline_updates_in_review_submissions(self):
        user = User.objects.create_user(email='r@test.com', password='pass')
        deadline = timezone.now() + timedelta(days=2)
        sub = ProjectSubmission.objects.create(
            user=user, course=self.course,
            project_url='https://example.com/x',
            status='in_review', review_deadline=deadline,
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/extend-deadline',
            {'days': 3}, follow=True,
        )
        self.assertContains(response, 'Extended deadline by 3 days for 1 submission(s).')
        sub.refresh_from_db()
        self.assertEqual(
            sub.review_deadline.date(),
            (deadline + timedelta(days=3)).date(),
        )
