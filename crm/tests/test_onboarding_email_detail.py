"""Detailed onboarding-completed staff email (full and sparse data)."""

from django.test import TestCase

from analytics.models import UserAttribution
from crm.services.onboarding_notify import build_onboarding_email
from payments.models import Membership, Tier
from questionnaires.models import (
    Answer,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
)
from tests.fixtures import create_user_with_membership

URL = 'https://example.test/studio/crm/7/#onboarding'


def _make_response(member):
    from questionnaires.models import Questionnaire
    questionnaire, _ = Questionnaire.objects.get_or_create(
        slug='onboarding-test', defaults={'title': 'Onboarding', 'purpose': 'onboarding'},
    )
    response = Response.objects.create(
        questionnaire=questionnaire, respondent=member, status='submitted',
    )
    q1 = ResponseQuestion.objects.create(
        response=response, question_type='text', order=1,
        prompt='What are you building?',
    )
    Answer.objects.create(response=response, question=q1, text_value='A <b>RAG</b> bot')
    q2 = ResponseQuestion.objects.create(
        response=response, question_type='multiple_choice', order=2,
        prompt='Goals',
    )
    a2 = Answer.objects.create(response=response, question=q2)
    for i, label in enumerate(['Ship an MVP', 'Learn evals']):
        opt = ResponseQuestionOption.objects.create(
            response_question=q2, label=label, order=i,
        )
        a2.selected_options.add(opt)
    ResponseQuestion.objects.create(
        response=response, question_type='text', order=3, prompt='Unanswered one',
    )
    return response


class OnboardingEmailDetailTest(TestCase):
    def test_full_data(self):
        member = create_user_with_membership(
            email='sample@test.com', password='pw', first_name='Sample',
            tier=Tier.objects.get(slug='main'),
        )
        member.email_verified = True
        member.preferred_timezone = 'Europe/Berlin'
        member.save()
        Membership.objects.filter(user=member).update(
            subscription_id='sub_123', stripe_customer_id='cus_1',
        )
        UserAttribution.objects.update_or_create(
            user=member, defaults={
                'first_touch_utm_source': 'newsletter',
                'first_touch_utm_campaign': 'launch',
            },
        )
        _make_response(member)
        member = type(member).objects.get(pk=member.pk)
        subject, text, html = build_onboarding_email(member, URL)
        self.assertIn('Onboarding completed: Sample <sample@test.com>', subject)
        self.assertTrue(subject.endswith('[Main]'))
        for needle in (
            'Plan: Main', 'Billing: Stripe subscription', 'Email verified: yes',
            'Timezone: Europe/Berlin', 'UTM source: newsletter',
            'UTM campaign: launch', 'What are you building?',
            'A <b>RAG</b> bot', 'Ship an MVP, Learn evals', URL,
        ):
            self.assertIn(needle, text)
        self.assertNotIn('Unanswered one', text)
        self.assertNotIn('None', text)
        self.assertIn('A &lt;b&gt;RAG&lt;/b&gt; bot', html)
        self.assertIn(f'<a href="{URL}">', html)

    def test_sparse_data_omits_missing_fields(self):
        member = create_user_with_membership(
            email='bare@test.com', password='pw',
            tier=Tier.objects.get(slug='free'),
        )
        subject, text, html = build_onboarding_email(member, URL, updated=True)
        self.assertIn('Onboarding answers updated: ', subject)
        self.assertIn('Email verified: no', text)
        self.assertNotIn('Onboarding answers\n', text)
        self.assertNotIn('Timezone', text)
        self.assertNotIn('UTM', text)
        self.assertNotIn('Billing', text)
        self.assertNotIn('None', text + html)
        self.assertNotIn('<h3>Onboarding answers', html)
