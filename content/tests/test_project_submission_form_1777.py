"""The shared project submission form on AISL (issue #1777).

The field contract and its validation are owned by the pinned
``community-base`` package (C5.2n); these tests cover the site-side adapter:
the dated attempt route, the undated legacy route, the JSON API, and the
capability rules AISL keeps (deadline and review locks, no self-service
removal, certificate name to the curriculum enrollment, legacy round-trip).
"""

import json
from datetime import timedelta

from community_base.curriculum.models import Enrollment as CurriculumEnrollment
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course, ProjectSubmission
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject
from content.services.project_form_adapter import (
    LEARNING_IN_PUBLIC_CAP,
    REMOVAL_UNAVAILABLE_MESSAGE,
    CourseProjectSubmissionTarget,
)

User = get_user_model()

GITHUB_URL = 'https://github.com/learner/project'
COMMIT = 'a1b2c3d'
CERT_NAME = 'Learner A. Display'


def _links(count):
    return [f'https://www.linkedin.com/posts/{number}' for number in range(count)]


class _SharedFormCourseTest(TestCase):
    """Shared fixtures: a peer-review course, a cohort learner, one attempt."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='learner1777@example.com', password='pw', email_verified=True,
            first_name='Learner',
        )
        cls.course = Course.objects.create(
            title='Shared Form Course', slug='shared-form',
            status='published', peer_review_enabled=True, peer_review_count=2,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 1777', external_key='1777',
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=90),
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.user)
        now = timezone.now()
        cls.attempt = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, slug='first',
            title='First attempt',
            submission_due_at=now + timedelta(days=1),
            review_due_at=now + timedelta(days=8),
        )

    def setUp(self):
        self.client.force_login(self.user)


class DatedAttemptSharedFormTest(_SharedFormCourseTest):
    """``/courses/<slug>/projects/<attempt>/submit`` renders the shared form."""

    submit_url = '/courses/shared-form/projects/first/submit'

    def test_get_renders_shared_field_sequence(self):
        response = self.client.get(self.submit_url)
        self.assertTemplateUsed(response, 'content/peer_review/submit.html')
        content = response.content.decode()
        # The shared contract's labels and help, in the shared order.
        self.assertIn('GitHub link to the project', content)
        self.assertIn('Commit ID', content)
        self.assertIn('Learning in public links', content)
        self.assertIn('Time spent on project (hours)', content)
        self.assertIn('Certificate name', content)
        self.assertIn('Where do I find the commit ID?', content)
        # Commit id is on and required for AISL attempts; the link cap is the
        # package default. FAQ contribution is a DataTalksClub-only field.
        self.assertIn('name="github_link"', content)
        self.assertIn('name="commit_id"', content)
        self.assertIn('Add up to 14 public progress links', content)
        self.assertNotIn('name="faq_contribution_url"', content)
        # The AISL-only legacy description rides along as the extra field.
        self.assertIn('name="description"', content)
        # Status line with save-now semantics.
        self.assertIn('Status: Not saved yet', content)
        self.assertIn('You can save your project now', content)
        # The partial's JS is collected (it drives + Add link and Remove).
        self.assertIsNotNone(
            finders.find('community_base/coursework_project_form.js'),
        )
        self.assertIn('/static/community_base/coursework_project_form.js', content)

    def test_save_round_trip_writes_submission_and_certificate_name(self):
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
            'learning_in_public_links': _links(2),
            'time_spent': '6.5',
            'certificate_name': CERT_NAME,
            'description': 'Built with FastAPI.',
        })
        self.assertContains(response, 'Your project has been submitted')
        submission = ProjectSubmission.objects.get(
            user=self.user, course_project=self.attempt,
        )
        self.assertEqual(submission.project_url, GITHUB_URL)
        self.assertEqual(submission.commit_id, COMMIT)
        self.assertEqual(submission.learning_in_public_links, _links(2))
        self.assertEqual(submission.time_spent, 6.5)
        self.assertEqual(submission.description, 'Built with FastAPI.')
        self.assertEqual(submission.cohort, self.cohort)
        # Certificate name goes to the curriculum enrollment, not the project.
        enrollment = CurriculumEnrollment.objects.get(
            user=self.user,
            cohort=self.cohort.curriculum_cohort,
            unenrolled_at__isnull=True,
        )
        self.assertEqual(enrollment.certificate_name, CERT_NAME)
        self.assertEqual(submission.status, 'submitted')

    def test_invalid_post_re_renders_entries_without_writes(self):
        before = CurriculumEnrollment.objects.count()
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': 'not-hex',
            'time_spent': '3',
            'description': 'A draft.',
        })
        self.assertEqual(response.status_code, 400)
        content = response.content.decode()
        self.assertIn('7 to 40 hexadecimal characters', content)
        self.assertIn('not-hex', content)  # raw entry echoed back
        self.assertIn('A draft.', content)
        self.assertFalse(
            ProjectSubmission.objects.filter(
                user=self.user, course_project=self.attempt,
            ).exists(),
        )
        self.assertEqual(CurriculumEnrollment.objects.count(), before)

    def test_github_link_must_be_a_github_repo(self):
        response = self.client.post(self.submit_url, {
            'github_link': 'https://example.com/learner/project',
            'commit_id': COMMIT,
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'Enter a GitHub repository link', status_code=400,
        )
        self.assertFalse(ProjectSubmission.objects.filter(user=self.user).exists())

    def test_time_spent_rejects_negative_hours(self):
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
            'time_spent': '-1',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'Ensure this value is greater than or equal to 0',
            status_code=400,
        )
        self.assertFalse(ProjectSubmission.objects.filter(user=self.user).exists())

    def test_learning_in_public_links_cap_trims_to_shared_cap(self):
        self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
            'learning_in_public_links': _links(LEARNING_IN_PUBLIC_CAP + 4),
        })
        submission = ProjectSubmission.objects.get(
            user=self.user, course_project=self.attempt,
        )
        self.assertEqual(
            len(submission.learning_in_public_links), LEARNING_IN_PUBLIC_CAP,
        )

    def test_learning_in_public_links_must_be_public_web_urls(self):
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
            'learning_in_public_links': ['http://localhost:8000/post'],
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'must be valid public HTTP or HTTPS URLs', status_code=400,
        )
        self.assertFalse(ProjectSubmission.objects.filter(user=self.user).exists())

    def test_forged_post_after_deadline_changes_nothing(self):
        self.attempt.submission_due_at = timezone.now() - timedelta(minutes=1)
        self.attempt.save(update_fields=['submission_due_at'])
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectSubmission.objects.filter(course_project=self.attempt).exists(),
        )

    def test_forged_post_after_review_lock_changes_nothing(self):
        submission = ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.attempt,
            cohort=self.cohort, project_url=GITHUB_URL, commit_id=COMMIT,
            status='in_review',
        )
        response = self.client.post(self.submit_url, {
            'github_link': 'https://github.com/learner/other',
            'commit_id': 'ffffff1',
        })
        self.assertEqual(response.status_code, 403)
        submission.refresh_from_db()
        self.assertEqual(submission.project_url, GITHUB_URL)
        self.assertEqual(submission.commit_id, COMMIT)

    def test_delete_action_is_rejected_without_side_effects(self):
        submission = ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.attempt,
            cohort=self.cohort, project_url=GITHUB_URL, commit_id=COMMIT,
        )
        response = self.client.post(self.submit_url, {
            'action': 'delete',
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
        })
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, REMOVAL_UNAVAILABLE_MESSAGE, status_code=403)
        self.assertTrue(
            ProjectSubmission.objects.filter(pk=submission.pk).exists(),
        )

    def test_legacy_submission_round_trip(self):
        submission = ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.attempt,
            cohort=self.cohort, project_url=GITHUB_URL,
            description='Legacy description.',
        )
        response = self.client.get(self.submit_url)
        self.assertTemplateUsed(response, 'content/peer_review/submit.html')
        content = response.content.decode()
        self.assertIn(f'value="{GITHUB_URL}"', content)
        self.assertContains(
            response, 'No commit ID was collected for this submission.',
        )
        self.assertIn('Legacy description.', content)
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
            'time_spent': '4',
            'description': 'Legacy description.',
        })
        self.assertContains(response, 'Your project has been submitted')
        submission.refresh_from_db()
        self.assertEqual(submission.commit_id, COMMIT)
        self.assertEqual(submission.time_spent, 4)
        self.assertEqual(submission.description, 'Legacy description.')

    def test_certificate_name_field_hidden_without_bridgeable_cohort(self):
        # No cohort-gated attempt and no enrollment: nothing can store a
        # certificate name, so the shared field must not render.
        CourseProject.objects.filter(pk=self.attempt.pk).update(cohort=None)
        CohortEnrollment.objects.filter(user=self.user).delete()
        response = self.client.get(self.submit_url)
        self.assertContains(response, 'GitHub link to the project')
        self.assertNotContains(response, 'name="certificate_name"')


class DatedAttemptDashboardCommitTest(_SharedFormCourseTest):
    """The attempt read view surfaces the stored commit id (issue #1777).

    Historical rows keep empty commit columns after migration; the dashboard
    shows them as missing, never fabricated (issue #1777 acceptance 5).
    """

    dashboard_url = '/courses/shared-form/projects/first/reviews'

    def test_dashboard_shows_stored_commit_id(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.attempt,
            cohort=self.cohort, project_url=GITHUB_URL, commit_id=COMMIT,
        )
        response = self.client.get(self.dashboard_url)
        self.assertTemplateUsed(response, 'content/peer_review/dashboard.html')
        self.assertContains(response, 'Commit ID')
        self.assertContains(response, 'data-testid="peer-submission-commit-id"')
        self.assertContains(response, COMMIT)

    def test_dashboard_shows_historical_commit_id_as_missing(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.attempt,
            cohort=self.cohort, project_url=GITHUB_URL,
        )
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, 'data-testid="peer-submission-commit-missing"')
        self.assertContains(response, 'Not collected')
        self.assertNotContains(response, 'data-testid="peer-submission-commit-id"')



class UndatedSubmitSharedFormTest(_SharedFormCourseTest):
    """``/courses/<slug>/submit`` stops showing a divergent form (#1777)."""

    submit_url = '/courses/shared-form/submit'

    def setUp(self):
        super().setUp()
        # The undated slot redirects to the attempt picker while a dated
        # attempt exists; these tests exercise the legacy slot itself. Delete
        # through a queryset: the shared setUpTestData instance must keep its
        # pk for the sibling test classes.
        CourseProject.objects.filter(course=self.course).delete()

    def test_get_renders_shared_fields(self):
        response = self.client.get(self.submit_url)
        self.assertTemplateUsed(response, 'content/peer_review/submit.html')
        content = response.content.decode()
        self.assertIn('GitHub link to the project', content)
        self.assertIn('name="commit_id"', content)
        self.assertIn('name="learning_in_public_links"', content)
        self.assertIn('Time spent on project (hours)', content)
        self.assertIn('name="certificate_name"', content)

    def test_self_paced_save_keeps_submission_cohortless_and_persists_name(self):
        cohort = Cohort.objects.get(pk=self.cohort.pk)
        cohort.mode = 'self_paced'
        cohort.save()
        response = self.client.post(self.submit_url, {
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
            'time_spent': '2',
            'certificate_name': CERT_NAME,
        })
        self.assertContains(response, 'Your project has been submitted')
        submission = ProjectSubmission.objects.get(
            user=self.user, course=self.course, course_project__isnull=True,
        )
        self.assertIsNone(submission.cohort_id)
        self.assertEqual(submission.commit_id, COMMIT)
        enrollment = CurriculumEnrollment.objects.get(
            user=self.user,
            cohort=self.cohort.curriculum_cohort,
            unenrolled_at__isnull=True,
        )
        self.assertEqual(enrollment.certificate_name, CERT_NAME)

    def test_locked_after_review_started(self):
        submission = ProjectSubmission.objects.create(
            user=self.user, course=self.course, project_url=GITHUB_URL,
            commit_id=COMMIT, status='in_review',
        )
        response = self.client.post(self.submit_url, {
            'github_link': 'https://github.com/learner/other',
            'commit_id': 'ffffff1',
        })
        self.assertContains(response, 'The submission form is closed.')
        submission.refresh_from_db()
        self.assertEqual(submission.project_url, GITHUB_URL)

    def test_delete_action_rejected(self):
        submission = ProjectSubmission.objects.create(
            user=self.user, course=self.course, project_url=GITHUB_URL,
            commit_id=COMMIT,
        )
        response = self.client.post(self.submit_url, {
            'action': 'delete',
            'github_link': GITHUB_URL,
            'commit_id': COMMIT,
        })
        self.assertContains(response, REMOVAL_UNAVAILABLE_MESSAGE)
        self.assertTrue(ProjectSubmission.objects.filter(pk=submission.pk).exists())


class ApiSubmitSharedFieldsTest(_SharedFormCourseTest):
    """``POST /api/courses/<slug>/submit`` takes the new fields optionally."""

    submit_url = '/api/courses/shared-form/submit'

    def setUp(self):
        super().setUp()
        # The API 409s toward the attempt picker while a dated attempt exists;
        # these tests cover the legacy slot the endpoint still serves. Delete
        # through a queryset: the shared setUpTestData instance must keep its
        # pk for the sibling test classes.
        CourseProject.objects.filter(course=self.course).delete()

    def _post(self, payload):
        return self.client.post(
            self.submit_url, data=json.dumps(payload),
            content_type='application/json',
        )

    def test_legacy_project_url_payload_still_works(self):
        response = self._post({
            'project_url': GITHUB_URL,
            'description': 'Posted by an old client.',
        })
        data = response.json()
        self.assertEqual(data['status'], 'submitted')
        submission = ProjectSubmission.objects.get(
            user=self.user, course=self.course, course_project__isnull=True,
        )
        self.assertEqual(submission.project_url, GITHUB_URL)
        self.assertEqual(submission.commit_id, '')
        self.assertIn('id', data)
        self.assertIn('project_url', data)
        self.assertIn('description', data)

    def test_new_fields_accepted_and_persisted(self):
        self._post({
            'project_url': GITHUB_URL,
            'commit_id': COMMIT,
            'learning_in_public_links': _links(2),
            'time_spent': 3,
            'certificate_name': CERT_NAME,
        })
        submission = ProjectSubmission.objects.get(
            user=self.user, course=self.course, course_project__isnull=True,
        )
        self.assertEqual(submission.commit_id, COMMIT)
        self.assertEqual(submission.learning_in_public_links, _links(2))
        self.assertEqual(submission.time_spent, 3)
        enrollment = CurriculumEnrollment.objects.get(
            user=self.user,
            cohort=self.cohort.curriculum_cohort,
            unenrolled_at__isnull=True,
        )
        self.assertEqual(enrollment.certificate_name, CERT_NAME)

    def test_invalid_commit_returns_field_errors_and_writes_nothing(self):
        response = self._post({'project_url': GITHUB_URL, 'commit_id': 'zzz'})
        self.assertEqual(response.status_code, 400)
        errors = response.json()['errors']
        self.assertIn('commit_id', errors)
        self.assertFalse(ProjectSubmission.objects.filter(user=self.user).exists())

    def test_locked_submission_rejects_update_without_side_effects(self):
        submission = ProjectSubmission.objects.create(
            user=self.user, course=self.course, project_url=GITHUB_URL,
            commit_id=COMMIT, status='in_review',
        )
        response = self._post({'project_url': 'https://github.com/learner/new'})
        self.assertEqual(response.status_code, 400)
        submission.refresh_from_db()
        self.assertEqual(submission.project_url, GITHUB_URL)
        self.assertEqual(submission.commit_id, COMMIT)


class SubmissionTargetTest(_SharedFormCourseTest):
    """The toggle wrapper maps AISL state onto the package contract."""

    def test_cmp_parity_toggles(self):
        target = CourseProjectSubmissionTarget(self.attempt)
        self.assertTrue(target.commit_id_field)
        self.assertTrue(target.time_spent_project_field)
        self.assertEqual(target.learning_in_public_cap_project, LEARNING_IN_PUBLIC_CAP)
        self.assertFalse(target.uses_pooled_review)
        self.assertEqual(
            target.submission_due_date, self.attempt.submission_due_at,
        )

    def test_state_reflects_the_learner_review_lock(self):
        unlocked = CourseProjectSubmissionTarget(self.attempt, submission=None)
        self.assertEqual(unlocked.state, 'CS')
        submitted = ProjectSubmission(
            status='submitted',
        )
        collecting = CourseProjectSubmissionTarget(self.attempt, submitted)
        self.assertEqual(collecting.state, 'CS')
        submitted.status = 'in_review'
        self.assertEqual(
            CourseProjectSubmissionTarget(self.attempt, submitted).state, 'PR',
        )

    def test_undated_slot_has_no_deadline(self):
        self.assertIsNone(CourseProjectSubmissionTarget(None).submission_due_date)
