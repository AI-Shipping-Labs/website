"""Tests for the form-first member onboarding flow (issue #802).

Covers self-ID routing (each archetype option -> its persona's
questionnaire; none/both -> generic), the persona-name-never-leaked
guarantee, completion gating / dashboard banner, resumability, access
control, and the graceful no-questionnaire degrade path.

The four personas and their onboarding questionnaires (plus the generic
``onboarding-general`` fallback) are seeded by migration
``questionnaires.0003`` and are available in the test DB.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from questionnaires.models import (
    Persona,
    Questionnaire,
    Response,
    ResponseQuestion,
)
from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG

User = get_user_model()

# The internal persona names must never reach the member.
PERSONA_NAMES = ['Alex', 'Priya', 'Sam', 'Taylor']


class OnboardingAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_landing_url_keeps_trailing_slash(self):
        # /onboarding/ is in SKIP_PREFIXES so the trailing slash survives.
        self.client.force_login(self.member)
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)


class SelfIdentificationRoutingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='router@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def _identify(self, value):
        return self.client.post(
            reverse('onboarding_identify'), {'self_id': value},
        )

    def test_each_persona_routes_to_its_default_questionnaire(self):
        personas = Persona.objects.filter(
            is_active=True, default_questionnaire__isnull=False,
        )
        self.assertTrue(personas.exists())
        for persona in personas:
            Response.objects.filter(respondent=self.member).delete()
            resp = self._identify(str(persona.pk))
            self.assertEqual(resp.status_code, 302)
            response = Response.objects.get(respondent=self.member)
            self.assertEqual(
                response.questionnaire_id, persona.default_questionnaire_id,
                f'persona {persona.slug} should route to its questionnaire',
            )

    def test_none_routes_to_generic(self):
        self._identify('none')
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(response.questionnaire.slug, GENERIC_ONBOARDING_SLUG)

    def test_more_than_one_routes_to_generic(self):
        self._identify('multiple')
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(response.questionnaire.slug, GENERIC_ONBOARDING_SLUG)

    def test_persona_without_questionnaire_falls_back_to_generic(self):
        orphan = Persona.objects.create(
            name='Orphan', archetype='The Persona With No Questionnaire',
            slug='orphan-persona', is_active=True, default_questionnaire=None,
        )
        self._identify(str(orphan.pk))
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(response.questionnaire.slug, GENERIC_ONBOARDING_SLUG)

    def test_identify_materializes_response_questions(self):
        self._identify('none')
        response = Response.objects.get(respondent=self.member)
        self.assertTrue(response.response_questions.exists())

    def test_identify_is_idempotent_no_duplicate_response(self):
        first = self._identify('none')
        first_id = Response.objects.get(respondent=self.member).pk
        # A second identify must not create a new response.
        self._identify('multiple')
        self.assertEqual(Response.objects.filter(respondent=self.member).count(), 1)
        self.assertEqual(
            Response.objects.get(respondent=self.member).pk, first_id,
        )
        self.assertEqual(first.status_code, 302)


class PersonaNameNeverLeaksTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='leak@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def _onboarding_form_html(self):
        """Return just the self-ID form markup, excluding site chrome.

        The base template's author meta tag contains "Alexey Grigorev",
        which is not a persona-name leak; the guarantee is about the
        onboarding content, so we scope to the identify form.
        """
        html = self.client.get('/onboarding/').content.decode()
        start = html.index('data-testid="onboarding-identify-form"')
        end = html.index('</form>', start)
        return html[start:end]

    def test_self_id_page_has_no_persona_names(self):
        form_html = self._onboarding_form_html()
        for name in PERSONA_NAMES:
            self.assertNotIn(
                name, form_html, f'persona name {name} leaked to member',
            )

    def test_self_id_page_shows_archetypes_as_labels(self):
        resp = self.client.get('/onboarding/')
        html = resp.content.decode()
        # Each active persona's archetype is rendered.
        for persona in Persona.objects.filter(
            is_active=True, default_questionnaire__isnull=False,
        ):
            self.assertIn(persona.archetype, html)
        # The two persona-agnostic options are offered.
        self.assertIn('None of these / not sure', html)
        self.assertIn('More than one / both', html)

    def test_option_values_carry_persona_pk_not_name(self):
        resp = self.client.get('/onboarding/')
        html = resp.content.decode()
        for persona in Persona.objects.filter(
            is_active=True, default_questionnaire__isnull=False,
        ):
            self.assertIn(f'value="{persona.pk}"', html)


class OnboardingCompletionGatingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='gate@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def test_dashboard_shows_prompt_before_onboarding(self):
        resp = self.client.get('/')
        self.assertContains(resp, 'data-testid="onboarding-prompt"')
        self.assertContains(resp, reverse('onboarding_start'))

    def test_dashboard_hides_prompt_after_submit(self):
        # Drive a full submit through the flow.
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        response = Response.objects.get(respondent=self.member)
        self._answer_required_and_submit(response)
        resp = self.client.get('/')
        self.assertNotContains(resp, 'data-testid="onboarding-prompt"')

    def _answer_required_and_submit(self, response):
        post = {}
        for rq in response.response_questions.filter(is_required=True):
            field = f'question_{rq.pk}'
            if rq.question_type in ('text', 'long_text'):
                post[field] = 'answer'
            elif rq.question_type in ('scale', 'number'):
                post[field] = '5'
            else:
                first_opt = rq.options.first()
                post[field] = str(first_opt.pk)
        return self.client.post(
            reverse('onboarding_submit', kwargs={'response_id': response.pk}),
            post,
        )


class OnboardingSubmitTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='submit@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.response = Response.objects.get(respondent=self.member)
        # The seeded generic questions are all optional; add one required
        # question so the submit-gating path is exercised.
        self.required = ResponseQuestion.objects.create(
            response=self.response, source_question=None,
            question_type='text', prompt='Required one-off question',
            is_required=True, order=999,
        )

    def test_submit_with_blank_required_re_renders_400_naming_question(self):
        required = self.required
        resp = self.client.post(
            reverse('onboarding_submit', kwargs={'response_id': self.response.pk}),
            {},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, required.prompt, status_code=400)
        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'draft')

    def test_submit_complete_marks_submitted_and_thanks(self):
        post = {}
        for rq in self.response.response_questions.filter(is_required=True):
            field = f'question_{rq.pk}'
            if rq.question_type in ('text', 'long_text'):
                post[field] = 'x'
            elif rq.question_type in ('scale', 'number'):
                post[field] = '3'
            else:
                post[field] = str(rq.options.first().pk)
        resp = self.client.post(
            reverse('onboarding_submit', kwargs={'response_id': self.response.pk}),
            post, follow=True,
        )
        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'submitted')
        messages = [m.message for m in resp.context['messages']]
        self.assertTrue(any('plan' in m.lower() for m in messages))


class OnboardingResumeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='resume@test.com', password='pw',
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.response = Response.objects.get(respondent=self.member)

    def test_revisit_start_with_draft_redirects_to_fill(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp['Location'],
            reverse('onboarding_fill', kwargs={'response_id': self.response.pk}),
        )

    def test_resume_prefills_saved_answers_no_second_response(self):
        # Save a draft answer.
        text_rq = self.response.response_questions.filter(
            question_type__in=('text', 'long_text'),
        ).first()
        self.client.post(
            reverse('onboarding_fill', kwargs={'response_id': self.response.pk}),
            {f'question_{text_rq.pk}': 'my draft answer'},
        )
        # Re-open the fill page; the saved value is pre-filled.
        resp = self.client.get(
            reverse('onboarding_fill', kwargs={'response_id': self.response.pk}),
        )
        self.assertContains(resp, 'my draft answer')
        self.assertEqual(
            Response.objects.filter(respondent=self.member).count(), 1,
        )


class OnboardingCompletedConfirmationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='done@test.com', password='pw',
        )
        generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
        cls.response = Response.objects.create(
            questionnaire=generic, respondent=cls.member, status='submitted',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def test_start_after_submit_shows_completion_not_restart(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="onboarding-complete-title"')
        # Not re-asking the self-ID question.
        self.assertNotContains(resp, 'data-testid="onboarding-identify-form"')

    def test_get_fill_after_submit_redirects_to_completion(self):
        resp = self.client.get(
            reverse('onboarding_fill', kwargs={'response_id': self.response.pk}),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))


class OnboardingCrossMemberIsolationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member_a = User.objects.create_user(
            email='a@test.com', password='pw',
        )
        cls.member_b = User.objects.create_user(
            email='b@test.com', password='pw',
        )
        generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
        cls.response_b = Response.objects.create(
            questionnaire=generic, respondent=cls.member_b, status='draft',
        )

    def test_member_cannot_open_another_members_response(self):
        self.client.force_login(self.member_a)
        resp = self.client.get(
            reverse('onboarding_fill', kwargs={'response_id': self.response_b.pk}),
        )
        self.assertEqual(resp.status_code, 404)


class OnboardingNotReadyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='notready@test.com', password='pw',
        )

    def setUp(self):
        # Simulate an environment where the onboarding questionnaires are
        # not seeded: detach persona questionnaires and remove the generic.
        Persona.objects.update(default_questionnaire=None)
        Questionnaire.objects.filter(purpose='onboarding').delete()
        self.client.force_login(self.member)

    def test_start_shows_not_ready_message_no_500(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="onboarding-not-ready"')

    def test_identify_without_questionnaire_no_500(self):
        resp = self.client.post(
            reverse('onboarding_identify'), {'self_id': 'none'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="onboarding-not-ready"')
        self.assertFalse(Response.objects.filter(respondent=self.member).exists())
