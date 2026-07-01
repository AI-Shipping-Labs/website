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
from django.test import TestCase, override_settings
from django.urls import reverse

# Issue #982: onboarding is gated to paid (effective tier >= Basic). The
# existing flow tests assume the member can actually enter onboarding, so
# they now create members on a paid (Basic) tier. The dedicated gating
# tests for Free / override members live in test_onboarding_gating_982.py.
from payments.models import Tier
from questionnaires.models import (
    Answer,
    Persona,
    Questionnaire,
    Response,
    ResponseQuestion,
)
from questionnaires.onboarding import GENERIC_ONBOARDING_SLUG

User = get_user_model()


def _basic_tier():
    return Tier.objects.get(slug='basic')


# The internal persona names must never reach the member.
PERSONA_NAMES = ['Alex', 'Priya', 'Sam', 'Taylor']


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class SelfIdentificationRoutingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='router@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class PersonaNameNeverLeaksTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='leak@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingCompletionGatingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='gate@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingSubmitTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='submit@test.com', password='pw', tier=_basic_tier(),
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

    def test_submit_requires_other_text_and_completion_displays_it(self):
        path_rq = self.response.response_questions.get(
            prompt='Which path best fits that goal?',
        )
        other = path_rq.options.get(label='Other')
        required_field = f'question_{self.required.pk}'
        path_field = f'question_{path_rq.pk}'
        other_text_field = f'question_{path_rq.pk}_option_{other.pk}_text'

        resp = self.client.post(
            reverse('onboarding_submit', kwargs={'response_id': self.response.pk}),
            {
                required_field: 'done',
                path_field: str(other.pk),
                other_text_field: '',
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertContains(
            resp,
            'Describe your &quot;Other&quot; answer.',
            status_code=400,
        )
        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'draft')

        resp = self.client.post(
            reverse('onboarding_submit', kwargs={'response_id': self.response.pk}),
            {
                required_field: 'done',
                path_field: str(other.pk),
                other_text_field: 'Help me choose between two project ideas',
            },
            follow=True,
        )
        self.response.refresh_from_db()
        self.assertEqual(self.response.status, 'submitted')
        self.assertContains(resp, 'Other: Help me choose between two project ideas')


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingResumeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='resume@test.com', password='pw', tier=_basic_tier(),
        )

    def setUp(self):
        self.client.force_login(self.member)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.response = Response.objects.get(respondent=self.member)

    def test_revisit_start_with_draft_redirects_to_questions(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_questions'))

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

    def test_save_draft_prefills_other_text(self):
        path_rq = self.response.response_questions.get(
            prompt='Which path best fits that goal?',
        )
        other = path_rq.options.get(label='Other')
        self.client.post(
            reverse('onboarding_fill', kwargs={'response_id': self.response.pk}),
            {
                f'question_{path_rq.pk}': str(other.pk),
                f'question_{path_rq.pk}_option_{other.pk}_text': (
                    'I need help choosing between two project ideas'
                ),
            },
        )
        resp = self.client.get(
            reverse('onboarding_fill', kwargs={'response_id': self.response.pk}),
        )
        self.assertContains(
            resp,
            'I need help choosing between two project ideas',
        )


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingCompletedConfirmationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='done@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingChangePersonaTest(TestCase):
    """#822: a member with a DRAFT can return and re-pick a persona."""

    WEEKLY_HOURS_PROMPT = (
        'How many hours per week can you realistically commit?'
    )

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='switcher@test.com', password='pw', tier=_basic_tier(),
        )
        personas = list(
            Persona.objects
            .filter(is_active=True, default_questionnaire__isnull=False)
            .order_by('order', 'name')
        )
        cls.persona_a = personas[0]
        cls.persona_b = personas[1]
        cls.generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)

    def setUp(self):
        self.client.force_login(self.member)

    def _identify(self, value):
        return self.client.post(
            reverse('onboarding_identify'), {'self_id': value},
        )

    def _rq(self, response, prompt):
        return response.response_questions.get(prompt=prompt)

    def test_fill_page_has_change_description_link(self):
        self._identify(str(self.persona_a.pk))
        response = Response.objects.get(respondent=self.member)
        resp = self.client.get(
            reverse('onboarding_fill', kwargs={'response_id': response.pk}),
        )
        self.assertContains(resp, 'data-testid="onboarding-change-description"')
        self.assertContains(resp, f"{reverse('onboarding_start')}?change=1")

    def test_change_request_shows_picker_with_current_selection(self):
        self._identify(str(self.persona_a.pk))
        resp = self.client.get(reverse('onboarding_start') + '?change=1')
        self.assertEqual(resp.status_code, 200)
        # The picker is re-shown (not redirected straight to fill).
        self.assertContains(resp, 'data-testid="onboarding-identify-form"')
        # The current persona's radio is pre-checked.
        html = resp.content.decode()
        marker = f'value="{self.persona_a.pk}"'
        idx = html.index(marker)
        self.assertIn('checked', html[idx:idx + 120])

    def test_draft_without_change_param_still_redirects_to_questions(self):
        self._identify(str(self.persona_a.pk))
        resp = self.client.get(reverse('onboarding_start'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_questions'))

    def test_reselect_repoints_draft_and_shows_new_persona_questions(self):
        self._identify(str(self.persona_a.pk))
        # Re-pick persona B.
        self._identify(str(self.persona_b.pk))
        self.assertEqual(
            Response.objects.filter(respondent=self.member).count(), 1,
        )
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(
            response.questionnaire_id, self.persona_b.default_questionnaire_id,
        )
        prompts = {rq.prompt for rq in response.response_questions.all()}
        for q in self.persona_b.default_questionnaire.questions.all():
            self.assertIn(q.prompt, prompts)

    def test_shared_answer_preserved_after_reselect(self):
        self._identify(str(self.persona_a.pk))
        response = Response.objects.get(respondent=self.member)
        hours_rq = self._rq(response, self.WEEKLY_HOURS_PROMPT)
        # Save the weekly-hours answer via the fill page.
        self.client.post(
            reverse('onboarding_fill', kwargs={'response_id': response.pk}),
            {f'question_{hours_rq.pk}': '12'},
        )
        # Re-pick persona B, then confirm the answer is pre-filled.
        self._identify(str(self.persona_b.pk))
        response.refresh_from_db()
        new_hours_rq = self._rq(response, self.WEEKLY_HOURS_PROMPT)
        answer = Answer.objects.get(response=response, question=new_hours_rq)
        self.assertEqual(answer.number_value, 12)

    def test_old_common_spine_answer_preserved_after_reselect(self):
        self._identify(str(self.persona_a.pk))
        response = Response.objects.get(respondent=self.member)
        old_prompt = (
            'How many hours per week can you realistically commit, consistently?'
        )
        hours_rq = self._rq(response, self.WEEKLY_HOURS_PROMPT)
        hours_rq.prompt = old_prompt
        hours_rq.save(update_fields=['prompt', 'updated_at'])
        Answer.objects.create(response=response, question=hours_rq, number_value=8)

        self._identify(str(self.persona_b.pk))
        response.refresh_from_db()
        new_hours_rq = self._rq(response, self.WEEKLY_HOURS_PROMPT)
        answer = Answer.objects.get(response=response, question=new_hours_rq)
        self.assertEqual(answer.number_value, 8)

    def test_switch_to_none_routes_to_generic_then_back_to_persona(self):
        self._identify(str(self.persona_a.pk))
        self._identify('none')
        response = Response.objects.get(respondent=self.member)
        self.assertEqual(response.questionnaire_id, self.generic.pk)
        # Re-pick a specific persona again.
        self._identify(str(self.persona_b.pk))
        response.refresh_from_db()
        self.assertEqual(
            response.questionnaire_id, self.persona_b.default_questionnaire_id,
        )

    def test_submitted_response_cannot_change_persona(self):
        # Submit against the generic questionnaire.
        sub = Response.objects.create(
            questionnaire=self.generic, respondent=self.member,
            status='submitted',
        )
        # GET start with change param: still shows completion, not picker.
        resp = self.client.get(reverse('onboarding_start') + '?change=1')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="onboarding-complete-title"')
        self.assertNotContains(resp, 'data-testid="onboarding-identify-form"')
        # POST identify: does not repoint; redirects to completion.
        resp = self._identify(str(self.persona_a.pk))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))
        sub.refresh_from_db()
        self.assertEqual(sub.questionnaire_id, self.generic.pk)


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingCrossMemberIsolationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member_a = User.objects.create_user(
            email='a@test.com', password='pw', tier=_basic_tier(),
        )
        cls.member_b = User.objects.create_user(
            email='b@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingNotReadyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='notready@test.com', password='pw', tier=_basic_tier(),
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


@override_settings(ONBOARDING_AI_ENABLED='false')
class OnboardingQuestionsIdFreeUrlTest(TestCase):
    """#819: the member-facing fill page is the id-free ``/onboarding/questions``.

    It resolves the requester's own draft server-side (no DB id in the URL)
    and never exposes another member's response.
    """

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='idfree@test.com', password='pw', tier=_basic_tier(),
        )
        cls.other = User.objects.create_user(
            email='idfree-other@test.com', password='pw', tier=_basic_tier(),
        )

    def setUp(self):
        self.client.force_login(self.member)

    def _start_draft(self):
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        return Response.objects.get(respondent=self.member)

    def test_questions_url_renders_own_draft_fill_page(self):
        response = self._start_draft()
        resp = self.client.get(reverse('onboarding_questions'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'accounts/onboarding_fill.html')
        self.assertEqual(resp.context['response'].pk, response.pk)

    def test_questions_url_with_no_draft_redirects_to_start(self):
        resp = self.client.get(reverse('onboarding_questions'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))

    def test_questions_resolves_requesters_own_draft_not_anothers(self):
        # Two members each with their own draft; the id-free URL must always
        # resolve the requester's own response regardless of pk ordering.
        own = self._start_draft()
        self.client.force_login(self.other)
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        other_response = Response.objects.get(respondent=self.other)
        self.assertNotEqual(own.pk, other_response.pk)
        resp = self.client.get(reverse('onboarding_questions'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['response'].pk, other_response.pk)

    def test_save_draft_redirects_to_id_free_url_and_keeps_answer(self):
        response = self._start_draft()
        text_rq = response.response_questions.filter(
            question_type__in=('text', 'long_text'),
        ).first()
        resp = self.client.post(
            reverse('onboarding_questions'),
            {f'question_{text_rq.pk}': 'my saved answer'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_questions'))
        # The saved answer is pre-filled on the next GET.
        follow = self.client.get(reverse('onboarding_questions'))
        self.assertContains(follow, 'my saved answer')

    def test_questions_url_after_submit_redirects_to_completion(self):
        response = self._start_draft()
        response.status = 'submitted'
        response.save(update_fields=['status'])
        resp = self.client.get(reverse('onboarding_questions'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_start'))
