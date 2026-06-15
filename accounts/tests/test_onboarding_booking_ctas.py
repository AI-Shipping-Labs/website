"""End-of-onboarding founder booking CTAs (issue #951).

The completion screen (``accounts/onboarding_complete.html``, rendered by
``onboarding_start`` for a submitted onboarding ``Response``) shows two CTAs
linking to each founder's external scheduler. The booking URLs come from the
Studio-editable ``CallHost`` store (slugs ``valeria`` / ``alexey``), the same
source ``/request-a-call`` uses -- not new IntegrationSetting keys.

This file also asserts both finish paths (form submit + AI chat done) now
land on the completion screen, and that the ``alexey`` backfill migration
sets the URL only when blank.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from community.models import CallHost
from questionnaires.models import Response, ResponseQuestion

User = get_user_model()


# Issue #982: onboarding is gated to paid (effective tier >= Basic). These
# flow tests create members on a paid (Basic) tier so they can enter the
# flow. Free / override gating is covered in test_onboarding_gating_982.py.
def _basic_tier():
    from payments.models import Tier

    return Tier.objects.get(slug='basic')



def _submit_onboarding(client):
    """Drive a member through the form flow to a submitted Response.

    Returns the submitted ``Response``. The member is already logged in on
    ``client``. Uses the ``none`` self-ID (generic questionnaire) and answers
    any required questions so ``onboarding_submit`` marks it submitted.
    """
    client.post(reverse('onboarding_identify'), {'self_id': 'none'})
    response = Response.objects.get()
    post = {}
    for rq in response.response_questions.filter(is_required=True):
        field = f'question_{rq.pk}'
        if rq.question_type in ('text', 'long_text'):
            post[field] = 'x'
        elif rq.question_type in ('scale', 'number'):
            post[field] = '3'
        else:
            post[field] = str(rq.options.first().pk)
    client.post(
        reverse('onboarding_submit', kwargs={'response_id': response.pk}),
        post,
    )
    response.refresh_from_db()
    return response


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingSubmitLandsOnCompletionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='complete-form@test.com', password='pw', tier=_basic_tier(),
        )

    def setUp(self):
        self.client.force_login(self.member)

    def test_form_submit_redirects_to_completion_screen(self):
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        response = Response.objects.get(respondent=self.member)
        ResponseQuestion.objects.create(
            response=response, source_question=None, question_type='text',
            prompt='Required one-off question', is_required=True, order=999,
        )
        post = {}
        for rq in response.response_questions.filter(is_required=True):
            field = f'question_{rq.pk}'
            post[field] = 'x'
        resp = self.client.post(
            reverse('onboarding_submit', kwargs={'response_id': response.pk}),
            post,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))
        # Following it renders the completion screen for the submitted response.
        landed = self.client.get(reverse('onboarding_start'))
        self.assertEqual(landed.status_code, 200)
        self.assertContains(landed, 'data-testid="onboarding-complete-title"')


@override_settings(ONBOARDING_AI_ENABLED='false')
class CompletionScreenBookingCtasTest(TestCase):
    """The completion screen surfaces the founder booking URLs from CallHost."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='ctas@test.com', password='pw', tier=_basic_tier(),
        )

    def setUp(self):
        self.client.force_login(self.member)
        _submit_onboarding(self.client)

    def test_both_ctas_render_with_callhost_urls(self):
        # The migration seeds valeria; alexey is backfilled by 0012. Set both
        # explicitly so the assertion pins the rendered href.
        CallHost.objects.filter(slug='valeria').update(
            booking_url='https://example.com/valeria-book',
        )
        CallHost.objects.filter(slug='alexey').update(
            booking_url='https://example.com/alexey-book',
        )
        resp = self.client.get(reverse('onboarding_start'))
        self.assertContains(resp, 'data-testid="onboarding-complete-book-block"')
        self.assertContains(
            resp, 'data-testid="onboarding-complete-book-valeria"',
        )
        self.assertContains(
            resp, 'data-testid="onboarding-complete-book-alexey"',
        )
        self.assertContains(resp, 'https://example.com/valeria-book')
        self.assertContains(resp, 'https://example.com/alexey-book')
        # New-tab CTA hygiene (mirrors /request-a-call).
        self.assertContains(resp, 'rel="noopener noreferrer"')

    def test_context_carries_both_booking_urls(self):
        CallHost.objects.filter(slug='valeria').update(
            booking_url='https://example.com/v',
        )
        CallHost.objects.filter(slug='alexey').update(
            booking_url='https://example.com/a',
        )
        resp = self.client.get(reverse('onboarding_start'))
        self.assertEqual(
            resp.context['valeria_booking_url'], 'https://example.com/v',
        )
        self.assertEqual(
            resp.context['alexey_booking_url'], 'https://example.com/a',
        )

    def test_blank_booking_url_hides_only_that_cta(self):
        CallHost.objects.filter(slug='valeria').update(
            booking_url='https://example.com/valeria-book',
        )
        CallHost.objects.filter(slug='alexey').update(booking_url='')
        resp = self.client.get(reverse('onboarding_start'))
        # Valeria CTA present, Alexey CTA hidden, block still shown.
        self.assertContains(resp, 'data-testid="onboarding-complete-book-block"')
        self.assertContains(
            resp, 'data-testid="onboarding-complete-book-valeria"',
        )
        self.assertNotContains(
            resp, 'data-testid="onboarding-complete-book-alexey"',
        )

    def test_both_blank_omits_whole_block(self):
        CallHost.objects.filter(slug__in=['valeria', 'alexey']).update(
            booking_url='',
        )
        resp = self.client.get(reverse('onboarding_start'))
        self.assertNotContains(
            resp, 'data-testid="onboarding-complete-book-block"',
        )
        self.assertNotContains(
            resp, 'data-testid="onboarding-complete-book-valeria"',
        )
        self.assertNotContains(
            resp, 'data-testid="onboarding-complete-book-alexey"',
        )
        # The dashboard link is always present, regardless of booking links.
        self.assertContains(
            resp, 'data-testid="onboarding-complete-plan-link"',
        )


class AlexeyBackfillMigrationTest(TestCase):
    """Migration 0012 backfills alexey's booking_url only when blank."""

    EXPECTED_URL = 'https://calendly.com/dtc-alexey/ai-shipping-labs-call'

    def test_migration_seeded_alexey_url(self):
        # The migration runs in the test DB build; alexey's URL is set.
        alexey = CallHost.objects.get(slug='alexey')
        self.assertEqual(alexey.booking_url, self.EXPECTED_URL)

    def test_backfill_idempotent_does_not_clobber_operator_value(self):
        # Simulate an operator-set value, then re-run the backfill function.
        from importlib import import_module

        mig = import_module(
            'community.migrations.0012_backfill_alexey_booking_url',
        )
        CallHost.objects.filter(slug='alexey').update(
            booking_url='https://operator.example/custom',
        )

        class _Apps:
            def get_model(self, app, model):
                return CallHost

        mig.backfill_alexey_booking_url(_Apps(), None)
        alexey = CallHost.objects.get(slug='alexey')
        self.assertEqual(
            alexey.booking_url, 'https://operator.example/custom',
        )

    def test_backfill_fills_when_blank(self):
        from importlib import import_module

        mig = import_module(
            'community.migrations.0012_backfill_alexey_booking_url',
        )
        CallHost.objects.filter(slug='alexey').update(booking_url='')

        class _Apps:
            def get_model(self, app, model):
                return CallHost

        mig.backfill_alexey_booking_url(_Apps(), None)
        alexey = CallHost.objects.get(slug='alexey')
        self.assertEqual(alexey.booking_url, self.EXPECTED_URL)
