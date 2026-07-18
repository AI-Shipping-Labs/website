import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from community.models import CommunityAuditLog
from questionnaires.models import Questionnaire, Response
from questionnaires.response_workflows import (
    ResponseNotSubmitted,
    transition_response_review,
)

User = get_user_model()


class ResponseReviewWorkflowTest(TestCase):
    def setUp(self):
        self.questionnaire = Questionnaire.objects.create(title='Operator queue')
        self.member = User.objects.create_user(email='member-1289@test.com')
        self.staff = User.objects.create_user(
            email='staff-1289@test.com', is_staff=True,
        )
        self.response = Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.member,
        )

    def test_review_and_reopen_are_idempotent_and_audit_only_transitions(self):
        self.response.mark_submitted()
        reviewed, changed = transition_response_review(
            response_id=self.response.pk, reviewed=True, actor=self.staff,
        )
        original_at = reviewed.reviewed_at
        self.assertTrue(changed)

        reviewed, changed = transition_response_review(
            response_id=self.response.pk, reviewed=True, actor=self.staff,
        )
        self.assertFalse(changed)
        self.assertEqual(reviewed.reviewed_at, original_at)
        self.assertEqual(CommunityAuditLog.objects.count(), 1)

        reopened, changed = transition_response_review(
            response_id=self.response.pk, reviewed=False, actor=self.staff,
        )
        self.assertTrue(changed)
        self.assertIsNone(reopened.reviewed_at)
        _, changed = transition_response_review(
            response_id=self.response.pk, reviewed=False, actor=self.staff,
        )
        self.assertFalse(changed)
        self.assertEqual(CommunityAuditLog.objects.count(), 2)

        details = json.loads(CommunityAuditLog.objects.order_by('pk').first().details)
        self.assertEqual(details['response_id'], self.response.pk)
        self.assertEqual(details['questionnaire_id'], self.questionnaire.pk)
        self.assertEqual(details['previous_review_state'], 'awaiting')
        self.assertEqual(details['new_review_state'], 'reviewed')
        self.assertEqual(details['actor'], self.staff.email)
        self.assertNotIn('answer', details)

    def test_draft_review_is_rejected_without_audit(self):
        with self.assertRaises(ResponseNotSubmitted):
            transition_response_review(
                response_id=self.response.pk, reviewed=True, actor=self.staff,
            )
        self.response.refresh_from_db()
        self.assertEqual(self.response.review_state, 'not_applicable')
        self.assertFalse(CommunityAuditLog.objects.exists())

    def test_questionnaire_scope_rejects_cross_parent_id(self):
        other = Questionnaire.objects.create(title='Other')
        with self.assertRaises(Response.DoesNotExist):
            transition_response_review(
                response_id=self.response.pk,
                reviewed=True,
                actor=self.staff,
                questionnaire_id=other.pk,
            )
