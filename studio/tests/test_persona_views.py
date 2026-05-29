"""Studio CRUD tests for internal-only personas (issue #801)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from questionnaires.models import Persona, Questionnaire
from tests.fixtures import StaffUserMixin

User = get_user_model()


@tag('core')
class PersonaStudioAccessTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw', is_staff=False,
        )
        cls.persona = Persona.objects.create(
            name='Alex', archetype='The Engineer transitioning to AI',
            slug='alex-access-test',
        )

    def test_anonymous_redirected_to_login(self):
        response = self.client.get('/studio/personas/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_forbidden_and_no_data_leak(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/studio/personas/')
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(
            response, 'The Engineer transitioning to AI', status_code=403,
        )


@tag('core')
class PersonaStudioCrudTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_shows_name_and_archetype_together(self):
        Persona.objects.create(name='Priya', archetype='The Improver', slug='priya-list-test')
        response = self.client.get('/studio/personas/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Priya')
        self.assertContains(response, 'The Improver')

    def test_empty_state_shown_when_no_personas(self):
        Persona.objects.all().delete()
        response = self.client.get('/studio/personas/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'New persona')

    def test_create_persona_redirects_to_detail_with_message(self):
        response = self.client.post('/studio/personas/new', {
            'name': 'Jordan',
            'archetype': 'The Career-switcher from a non-technical field',
            'description': 'Switching careers.',
            'is_active': 'on',
            'order': '5',
        })
        persona = Persona.objects.get(name='Jordan')
        self.assertRedirects(response, f'/studio/personas/{persona.pk}/')
        self.assertEqual(persona.slug, 'jordan')
        self.assertEqual(
            persona.archetype, 'The Career-switcher from a non-technical field',
        )
        followed = self.client.get(f'/studio/personas/{persona.pk}/')
        self.assertContains(followed, 'created')

    def test_create_without_archetype_rejected_400_no_row(self):
        before = Persona.objects.count()
        response = self.client.post('/studio/personas/new', {
            'name': 'Nameless',
            'archetype': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Archetype is required.', status_code=400)
        self.assertEqual(Persona.objects.count(), before)

    def test_edit_persona_sets_default_questionnaire(self):
        persona = Persona.objects.create(name='Sam', archetype='The Tech Pro', slug='sam-set-dq')
        questionnaire = Questionnaire.objects.create(
            title='Onboarding Sam', slug='onb-sam-setdq', purpose='onboarding',
        )
        response = self.client.post(
            f'/studio/personas/{persona.pk}/edit',
            {
                'name': 'Sam',
                'slug': persona.slug,
                'archetype': 'The Tech Pro',
                'default_questionnaire': str(questionnaire.pk),
                'is_active': 'on',
                'order': '0',
            },
        )
        self.assertRedirects(response, f'/studio/personas/{persona.pk}/')
        persona.refresh_from_db()
        self.assertEqual(persona.default_questionnaire, questionnaire)

    def test_edit_persona_clears_default_questionnaire(self):
        questionnaire = Questionnaire.objects.create(
            title='Onboarding Sam', slug='onb-sam-cleardq', purpose='onboarding',
        )
        persona = Persona.objects.create(
            name='Sam', archetype='The Tech Pro', slug='sam-clear-dq',
            default_questionnaire=questionnaire,
        )
        self.client.post(
            f'/studio/personas/{persona.pk}/edit',
            {
                'name': 'Sam',
                'slug': persona.slug,
                'archetype': 'The Tech Pro',
                'default_questionnaire': '',
                'is_active': 'on',
                'order': '0',
            },
        )
        persona.refresh_from_db()
        self.assertIsNone(persona.default_questionnaire)

    def test_default_questionnaire_dropdown_only_onboarding(self):
        onboarding = Questionnaire.objects.create(
            title='Onboarding Set ZZ', purpose='onboarding',
        )
        feedback = Questionnaire.objects.create(
            title='Feedback Set YY', purpose='feedback',
        )
        persona = Persona.objects.create(name='Sam', archetype='The Tech Pro', slug='sam-dropdown')
        response = self.client.get(f'/studio/personas/{persona.pk}/edit')
        self.assertContains(response, onboarding.title)
        self.assertNotContains(response, feedback.title)

    def test_non_onboarding_questionnaire_rejected_on_submit(self):
        feedback = Questionnaire.objects.create(
            title='Feedback Set', purpose='feedback',
        )
        persona = Persona.objects.create(name='Sam', archetype='The Tech Pro', slug='sam-reject-fb')
        response = self.client.post(
            f'/studio/personas/{persona.pk}/edit',
            {
                'name': 'Sam',
                'slug': persona.slug,
                'archetype': 'The Tech Pro',
                'default_questionnaire': str(feedback.pk),
                'is_active': 'on',
                'order': '0',
            },
        )
        self.assertEqual(response.status_code, 400)
        persona.refresh_from_db()
        self.assertIsNone(persona.default_questionnaire)

    def test_detail_shows_archetype_and_none_questionnaire_state(self):
        persona = Persona.objects.create(
            name='Taylor', archetype='The Researcher',
            description='Strong theory.', slug='taylor-detail',
        )
        response = self.client.get(f'/studio/personas/{persona.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The Researcher')
        self.assertContains(response, 'Strong theory.')
        self.assertContains(response, 'No onboarding questionnaire linked')

    def test_edited_archetype_shows_on_detail_and_list(self):
        persona = Persona.objects.create(name='Sam', archetype='Old archetype', slug='sam-edit-arch')
        self.client.post(
            f'/studio/personas/{persona.pk}/edit',
            {
                'name': 'Sam',
                'slug': persona.slug,
                'archetype': 'New shiny archetype',
                'is_active': 'on',
                'order': '0',
            },
        )
        detail = self.client.get(f'/studio/personas/{persona.pk}/')
        self.assertContains(detail, 'New shiny archetype')
        listing = self.client.get('/studio/personas/')
        self.assertContains(listing, 'New shiny archetype')
        self.assertNotContains(listing, 'Old archetype')
