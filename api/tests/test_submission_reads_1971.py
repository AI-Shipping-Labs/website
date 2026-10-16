"""Read-only homework submission API contracts (#1971)."""

from community_base.mail.models import EmailDelivery

from accounts.models import EmailAlias
from api.tests.test_submissions_on_behalf import URL, OnBehalfBase, audit_rows
from content.models import CohortEnrollment
from content.models.homework import Submission
from content.services.homework_submissions import save_submission
from notifications.models import Notification


class HomeworkShowTest(OnBehalfBase):
    def _side_effect_counts(self):
        return (
            audit_rows().count(), Notification.objects.count(),
            EmailDelivery.objects.count(), CohortEnrollment.objects.count(),
        )

    def _save_learner_submission(self):
        save_submission(
            homework=self.homework, user=self.member, homework_link='',
            answers_by_question_id={self.idea.pk: 'Learner answer'},
        )

    def test_returns_learner_submission_by_alias_without_writes(self):
        EmailAlias.objects.create(user=self.member, email='old@test.com')
        self._save_learner_submission()
        counts = self._side_effect_counts()

        response = self.client.get(
            URL, {'user': 'old@test.com', 'homework_id': self.homework.pk},
            **self.auth(),
        )

        data = response.json()
        self.assertEqual(data['user']['email'], 'member@test.com')
        self.assertEqual(data['submission']['answers'][0]['question_key'], 'project-idea')
        self.assertIsNone(data['submission']['submitted_on_behalf_by'])
        self.assertEqual(data['submission']['on_behalf_note'], '')
        self.assertEqual(counts, self._side_effect_counts())

    def test_missing_submission_returns_structured_404(self):
        response = self.client.get(
            URL,
            {
                'user': str(self.member.pk), 'course': self.course.slug,
                'homework': self.homework.slug, 'cohort': self.cohort.external_key,
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'submission_not_found')

    def test_returns_staff_actor_and_note(self):
        self.post(self.homework_body())
        response = self.client.get(
            URL, {'user': self.member.email, 'homework_id': self.homework.pk},
            **self.auth(),
        )

        submission = response.json()['submission']
        self.assertEqual(submission['submitted_on_behalf_by'], self.staff.email)
        self.assertEqual(submission['on_behalf_note'], 'Sent by email on 2026-09-01')

    def test_get_auth_and_target_validation(self):
        unauthorized = self.client.get(URL, {'user': self.member.email})
        self.assertEqual(unauthorized.status_code, 401)
        missing_target = self.client.get(URL, **self.auth())
        self.assertEqual(missing_target.status_code, 422)
        self.assertEqual(missing_target.json()['code'], 'missing_field')

    def test_conflict_points_to_show_and_preserves_existing_details(self):
        self._save_learner_submission()
        existing = Submission.objects.get()
        response = self.post(self.homework_body())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'submission_exists')
        self.assertIn('asl homework show', response.json()['error'])
        self.assertEqual(
            response.json()['details']['existing_submission']['id'], existing.pk,
        )
