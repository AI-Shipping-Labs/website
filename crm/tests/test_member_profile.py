"""Tests for the member-profile context service (issue #883).

``crm.services.member_profile.build_member_profile_context`` assembles the
read-only profile shown on the plan-create page: onboarding answers (reusing
the #871 flattening helper), CRM persona/summary/next-steps, and recent
internal notes. These tests cover the populated case, the no-onboarding /
no-CRM fallback, and that only INTERNAL notes are surfaced.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from analytics.models import UserActivity
from crm.models import CRMRecord
from crm.services.member_profile import build_member_profile_context
from plans.models import InterviewNote
from questionnaires.models import (
    Answer,
    Persona,
    Questionnaire,
    Response,
    ResponseQuestion,
)

User = get_user_model()


def _onboarding_questionnaire():
    # Seeded by the questionnaires data migration.
    return Questionnaire.objects.get(slug='onboarding-general')


def _submitted_response(member, *, qa):
    """Create a submitted onboarding response with the given Q/A list.

    ``qa`` is a list of ``(prompt, text_value)`` tuples.
    """
    response = Response.objects.create(
        questionnaire=_onboarding_questionnaire(),
        respondent=member,
        status='submitted',
    )
    for order, (prompt, text) in enumerate(qa):
        rq = ResponseQuestion.objects.create(
            response=response, question_type='long_text',
            prompt=prompt, order=order,
        )
        Answer.objects.create(
            response=response, question=rq, text_value=text,
        )
    return response


class MemberProfilePopulatedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='A',
        )
        _submitted_response(cls.member, qa=[
            ('What are your goals?', 'Switch into an AI engineering role'),
            ('Background?', 'Ten years of backend Java'),
        ])
        cls.record = CRMRecord.objects.create(
            user=cls.member, persona='Sam — Technical Professional',
            summary='Strong engineer, needs portfolio',
            next_steps='Ship a RAG project this sprint',
        )
        InterviewNote.objects.create(
            member=cls.member, visibility='internal',
            body='Very motivated on the intro call',
        )
        UserActivity.objects.all().delete()

    def test_includes_onboarding_answers(self):
        ctx = build_member_profile_context(self.member)
        self.assertTrue(ctx['has_onboarding'])
        self.assertTrue(ctx['onboarding_submitted'])
        prompts = {row['prompt'] for row in ctx['onboarding_answers']}
        self.assertIn('What are your goals?', prompts)
        displays = {row['display'] for row in ctx['onboarding_answers']}
        self.assertIn('Switch into an AI engineering role', displays)

    def test_includes_crm_persona_summary_next_steps(self):
        ctx = build_member_profile_context(self.member)
        self.assertTrue(ctx['has_crm_record'])
        self.assertEqual(ctx['persona'], 'Sam — Technical Professional')
        self.assertEqual(ctx['summary'], 'Strong engineer, needs portfolio')
        self.assertEqual(ctx['next_steps'], 'Ship a RAG project this sprint')

    def test_includes_recent_internal_notes(self):
        ctx = build_member_profile_context(self.member)
        self.assertTrue(ctx['has_notes'])
        bodies = [n.body for n in ctx['recent_notes']]
        self.assertIn('Very motivated on the intro call', bodies)

    def test_copy_text_includes_goals_persona_and_next_steps(self):
        ctx = build_member_profile_context(self.member)
        copy_text = ctx['copy_text']
        self.assertIn('Switch into an AI engineering role', copy_text)
        self.assertIn('Sam — Technical Professional', copy_text)
        self.assertIn('Ship a RAG project this sprint', copy_text)
        self.assertIn('alice@test.com', copy_text)

    def test_persona_ref_label_preferred_when_set(self):
        persona = Persona.objects.create(
            name='Priya', archetype='The Engineer transitioning to AI',
            slug='priya-883',
        )
        self.record.persona_ref = persona
        self.record.save(update_fields=['persona_ref'])
        ctx = build_member_profile_context(self.member)
        self.assertEqual(ctx['persona'], persona.display_label)

    def test_recent_activity_included_in_context_and_copy_text(self):
        UserActivity.objects.create(
            user=self.member,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            label='Viewed article: RAG patterns',
            target_url='/blog/rag-patterns',
            occurred_at=timezone.now(),
        )

        ctx = build_member_profile_context(self.member)

        self.assertTrue(ctx['has_recent_activity'])
        self.assertEqual(len(ctx['recent_activity']), 1)
        self.assertEqual(
            ctx['recent_activity'][0]['label'],
            'Viewed article: RAG patterns',
        )
        self.assertIn('Recent activity:', ctx['copy_text'])
        self.assertIn('Viewed article: RAG patterns', ctx['copy_text'])


class MemberProfileNotesScopingTest(TestCase):
    def test_only_internal_notes_surfaced(self):
        member = User.objects.create_user(email='bob@test.com', password='pw')
        InterviewNote.objects.create(
            member=member, visibility='internal', body='internal only note',
        )
        InterviewNote.objects.create(
            member=member, visibility='external', body='shared external note',
        )
        ctx = build_member_profile_context(member)
        bodies = [n.body for n in ctx['recent_notes']]
        self.assertIn('internal only note', bodies)
        self.assertNotIn('shared external note', bodies)
        # The external note must not leak into the copyable block either.
        self.assertNotIn('shared external note', ctx['copy_text'])


class MemberProfileEmptyFallbackTest(TestCase):
    def test_no_onboarding_no_crm_no_notes(self):
        member = User.objects.create_user(email='empty@test.com', password='pw')
        UserActivity.objects.filter(user=member).delete()
        ctx = build_member_profile_context(member)
        self.assertFalse(ctx['has_onboarding'])
        self.assertFalse(ctx['onboarding_submitted'])
        self.assertEqual(ctx['onboarding_answers'], [])
        self.assertFalse(ctx['has_crm_record'])
        self.assertEqual(ctx['persona'], '')
        self.assertEqual(ctx['summary'], '')
        self.assertEqual(ctx['next_steps'], '')
        self.assertFalse(ctx['has_notes'])
        self.assertEqual(ctx['recent_notes'], [])
        self.assertFalse(ctx['has_recent_activity'])
        self.assertEqual(ctx['recent_activity'], [])
        # Nothing substantive to copy beyond the header -> empty copy block.
        self.assertEqual(ctx['copy_text'], '')

    def test_onboarding_present_but_no_crm_record(self):
        member = User.objects.create_user(email='midd@test.com', password='pw')
        _submitted_response(member, qa=[('Goals?', 'Learn agents')])
        ctx = build_member_profile_context(member)
        self.assertTrue(ctx['has_onboarding'])
        self.assertFalse(ctx['has_crm_record'])
        self.assertEqual(ctx['persona'], '')
        # Copy text still has the onboarding answer even without a CRM record.
        self.assertIn('Learn agents', ctx['copy_text'])
