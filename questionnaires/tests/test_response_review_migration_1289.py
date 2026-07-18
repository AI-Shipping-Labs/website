from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ResponseReviewMigrationTest(TransactionTestCase):
    migrate_from = [('questionnaires', '0007_onboarding_turn_attempt')]
    migrate_to = [('questionnaires', '0008_response_review_queue')]

    def test_historical_submissions_grandfathered_and_drafts_untouched(self):
        current_users = [
            get_user_model().objects.create_user(
                email=f'migration-{index}@test.com',
            )
            for index in range(3)
        ]
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            Questionnaire = old_apps.get_model('questionnaires', 'Questionnaire')
            Response = old_apps.get_model('questionnaires', 'Response')
            ResponseQuestion = old_apps.get_model(
                'questionnaires', 'ResponseQuestion',
            )
            Answer = old_apps.get_model('questionnaires', 'Answer')

            questionnaire = Questionnaire.objects.create(
                title='Historical', slug='historical-1289', purpose='onboarding',
            )
            submitted = Response.objects.create(
                questionnaire=questionnaire, respondent_id=current_users[0].pk,
                status='submitted',
                submitted_at=datetime(2026, 1, 2, tzinfo=dt_timezone.utc),
            )
            legacy = Response.objects.create(
                questionnaire=questionnaire, respondent_id=current_users[1].pk,
                status='submitted', submitted_at=None,
            )
            fallback = datetime(2025, 12, 3, tzinfo=dt_timezone.utc)
            Response.objects.filter(pk=legacy.pk).update(updated_at=fallback)
            draft = Response.objects.create(
                questionnaire=questionnaire,
                respondent_id=current_users[2].pk,
                status='draft',
            )
            snapshot_question = ResponseQuestion.objects.create(
                response=submitted, question_type='text',
                prompt='Historical answer?', order=0,
            )
            answer = Answer.objects.create(
                response=submitted,
                question=snapshot_question,
                text_value='Preserve this exact answer',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewResponse = new_apps.get_model('questionnaires', 'Response')
            NewAnswer = new_apps.get_model('questionnaires', 'Answer')

            migrated_submitted = NewResponse.objects.get(pk=submitted.pk)
            self.assertEqual(
                migrated_submitted.reviewed_at, submitted.submitted_at,
            )
            self.assertIsNone(migrated_submitted.reviewed_by_id)
            migrated_legacy = NewResponse.objects.get(pk=legacy.pk)
            self.assertEqual(migrated_legacy.reviewed_at, fallback)
            self.assertIsNone(migrated_legacy.reviewed_by_id)
            migrated_draft = NewResponse.objects.get(pk=draft.pk)
            self.assertIsNone(migrated_draft.reviewed_at)
            self.assertIsNone(migrated_draft.reviewed_by_id)
            self.assertEqual(migrated_draft.status, 'draft')
            self.assertEqual(
                NewAnswer.objects.get(pk=answer.pk).text_value,
                'Preserve this exact answer',
            )
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
