"""Accessible-name regression coverage for questionnaire forms (#1531)."""

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase, tag

from questionnaires.models import (
    Questionnaire,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
)
from questionnaires.services import build_response_form_rows
from tests.fixtures import StaffUserMixin

User = get_user_model()


class _Node:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.text_parts = []

    @property
    def text(self):
        return ' '.join(''.join(self.text_parts).split())


class _Document(HTMLParser):
    _VOID_TAGS = {'input', 'meta', 'link', 'br', 'hr', 'img'}

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.nodes.append(node)
        if tag not in self._VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node.text_parts.append(data)

    def find(self, tag, **attrs):
        for node in self.nodes:
            if node.tag == tag and all(node.attrs.get(key) == value for key, value in attrs.items()):
                return node
        self.fail(f'No <{tag}> matched {attrs}')

    def find_all(self, tag, **attrs):
        return [
            node for node in self.nodes
            if node.tag == tag
            and all(node.attrs.get(key) == value for key, value in attrs.items())
        ]

    @staticmethod
    def fail(message):
        raise AssertionError(message)


@tag('core')
class MemberQuestionnaireAccessibleMarkupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        questionnaire = Questionnaire.objects.create(title='Accessible feedback')
        member = User.objects.create_user(email='member-1531@test.com')
        cls.response = Response.objects.create(
            questionnaire=questionnaire,
            respondent=member,
        )
        cls.questions = {}
        for order, (question_type, prompt) in enumerate((
            ('text', 'What is your current role?'),
            ('long_text', 'How did this sprint go for you?'),
            ('scale', 'How confident are you?'),
            ('number', 'How many hours?'),
            ('single_choice', 'How did you find us?'),
            ('multiple_choice', 'Which topics do you want?'),
        )):
            question = ResponseQuestion.objects.create(
                response=cls.response,
                question_type=question_type,
                prompt=prompt,
                help_text={
                    'text': 'Job title is enough',
                    'single_choice': 'Choose one answer',
                }.get(question_type, ''),
                is_required=question_type in {'long_text', 'single_choice'},
                scale_min=1 if question_type == 'scale' else None,
                scale_max=5 if question_type == 'scale' else None,
                order=order,
            )
            cls.questions[question_type] = question

        single = cls.questions['single_choice']
        ResponseQuestionOption.objects.create(
            response_question=single, label='Search', order=0,
        )
        cls.other = ResponseQuestionOption.objects.create(
            response_question=single,
            label='Other',
            allows_free_text=True,
            order=1,
        )
        multiple = cls.questions['multiple_choice']
        for order, label in enumerate(('Agents', 'RAG', 'Evals')):
            ResponseQuestionOption.objects.create(
                response_question=multiple, label=label, order=order,
            )

    def _document(self):
        errors = {
            self.questions['long_text'].pk: 'This question is required.',
            self.questions['single_choice'].pk: 'Pick one option.',
        }
        rows = build_response_form_rows(self.response, field_errors=errors)
        html = render_to_string('questionnaires/_response_form.html', {
            'form_rows': rows,
            'save_action': '/save',
            'submit_action': '/submit',
        })
        return _Document(html)

    def test_choice_groups_options_and_free_text_have_native_names(self):
        document = self._document()
        single = self.questions['single_choice']
        multiple = self.questions['multiple_choice']

        fieldsets = document.find_all('fieldset')
        self.assertEqual(len(fieldsets), 2)
        legends = document.find_all('legend')
        self.assertEqual(
            [legend.text for legend in legends],
            ['How did you find us? *', 'Which topics do you want?'],
        )
        self.assertEqual(fieldsets[0].attrs['aria-invalid'], 'true')
        self.assertEqual(
            fieldsets[0].attrs['aria-describedby'],
            f'question_{single.pk}-help question_{single.pk}-error',
        )
        self.assertEqual(
            document.find('p', id=f'question_{single.pk}-help').text,
            'Choose one answer',
        )
        self.assertFalse(document.find_all('label', **{'for': f'question_{single.pk}'}))
        self.assertFalse(document.find_all('label', **{'for': f'question_{multiple.pk}'}))

        choice_controls = document.find_all('input', type='radio') + document.find_all(
            'input', type='checkbox',
        )
        choice_ids = [control.attrs['id'] for control in choice_controls]
        self.assertEqual(len(choice_ids), len(set(choice_ids)))
        for control in choice_controls:
            label = document.find('label', **{'for': control.attrs['id']})
            self.assertIn(label.text, {'Search', 'Other', 'Agents', 'RAG', 'Evals'})

        free_text_name = f'question_{single.pk}_option_{self.other.pk}_text'
        free_text = document.find(
            'input', **{'data-testid': 'questionnaire-option-free-text'},
        )
        self.assertEqual(free_text.attrs['name'], free_text_name)
        self.assertEqual(free_text.attrs['id'], free_text_name)
        self.assertEqual(
            document.find('label', **{'for': free_text_name}).text,
            'Other details',
        )

    def test_single_value_controls_keep_labels_help_and_error_associations(self):
        document = self._document()
        for question_type in ('text', 'long_text', 'scale', 'number'):
            question = self.questions[question_type]
            control_id = f'question_{question.pk}'
            label = document.find('label', **{'for': control_id})
            self.assertEqual(label.text.rstrip(' *'), question.prompt)

        text_id = f"question_{self.questions['text'].pk}"
        text_control = document.find('input', id=text_id)
        self.assertEqual(text_control.attrs['aria-describedby'], f'{text_id}-help')
        self.assertEqual(document.find('p', id=f'{text_id}-help').text, 'Job title is enough')

        long_id = f"question_{self.questions['long_text'].pk}"
        long_control = document.find('textarea', id=long_id)
        self.assertEqual(long_control.attrs['aria-invalid'], 'true')
        self.assertEqual(long_control.attrs['aria-describedby'], f'{long_id}-error')
        self.assertEqual(
            document.find('p', id=f'{long_id}-error').attrs['data-testid'],
            'questionnaire-field-error',
        )


@tag('core')
class StudioQuestionnaireAccessibleMarkupTest(StaffUserMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.questionnaire = Questionnaire.objects.create(title='Studio labels')
        cls.member = User.objects.create_user(email='custom-1531@test.com')
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire,
            respondent=cls.member,
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_questionnaire_form_labels_help_and_title_error(self):
        document = _Document(self.client.get('/studio/questionnaires/new').content.decode())
        expected = {
            'questionnaire-title': 'Title',
            'questionnaire-slug': 'Slug',
            'questionnaire-purpose': 'Purpose',
            'questionnaire-description': 'Description',
            'questionnaire-is-active': 'Active',
        }
        for control_id, label_text in expected.items():
            self.assertEqual(document.find('label', **{'for': control_id}).text, label_text)
            self.assertTrue(document.find_all('input', id=control_id) or document.find_all('select', id=control_id) or document.find_all('textarea', id=control_id))
        self.assertEqual(
            document.find('input', id='questionnaire-slug').attrs['aria-describedby'],
            'questionnaire-slug-help',
        )
        self.assertEqual(
            document.find('input', id='questionnaire-is-active').attrs['aria-describedby'],
            'questionnaire-active-help',
        )

        invalid = _Document(self.client.post('/studio/questionnaires/new', {
            'title': '', 'purpose': 'general',
        }).content.decode())
        banner = invalid.find('div', id='questionnaire-form-error')
        self.assertEqual(banner.attrs['role'], 'alert')
        title = invalid.find('input', id='questionnaire-title')
        self.assertEqual(title.attrs['aria-invalid'], 'true')
        self.assertEqual(title.attrs['aria-describedby'], 'questionnaire-form-error')

        duplicate = Questionnaire.objects.create(
            title='Existing slug', slug='existing-slug-1531',
        )
        slug_invalid = _Document(self.client.post('/studio/questionnaires/new', {
            'title': 'Different title',
            'slug': duplicate.slug,
            'purpose': 'general',
        }).content.decode())
        slug = slug_invalid.find('input', id='questionnaire-slug')
        self.assertEqual(slug.attrs['aria-invalid'], 'true')
        self.assertEqual(
            slug.attrs['aria-describedby'],
            'questionnaire-slug-help questionnaire-form-error',
        )

    def _assert_question_authoring_contract(self, path, prefix, error_testid):
        document = _Document(self.client.get(path).content.decode())
        expected = {
            f'{prefix}-type': 'Type',
            f'{prefix}-prompt': 'Prompt',
            f'{prefix}-help-text': 'Help text',
            f'{prefix}-is-required': 'Required',
            f'{prefix}-order': 'Order',
            f'{prefix}-scale-min': 'Scale min',
            f'{prefix}-scale-max': 'Scale max',
            f'{prefix}-options': 'Choice options',
        }
        for control_id, label_text in expected.items():
            self.assertEqual(document.find('label', **{'for': control_id}).text, label_text)
        for suffix in ('scale-min', 'scale-max'):
            control = document.find('input', id=f'{prefix}-{suffix}')
            self.assertEqual(control.attrs['aria-describedby'], f'{prefix}-scale-help')
        options = document.find('textarea', id=f'{prefix}-options')
        self.assertEqual(options.attrs['aria-describedby'], f'{prefix}-options-help')

        invalid = _Document(self.client.post(path, {
            'question_type': 'text', 'prompt': '', 'order': '0',
        }).content.decode())
        banner = invalid.find('div', **{'data-testid': error_testid})
        self.assertEqual(banner.attrs['role'], 'alert')
        prompt = invalid.find('textarea', id=f'{prefix}-prompt')
        self.assertEqual(prompt.attrs['aria-invalid'], 'true')
        self.assertEqual(prompt.attrs['aria-describedby'], banner.attrs['id'])

    def test_shared_question_form_labels_help_and_prompt_error(self):
        self._assert_question_authoring_contract(
            f'/studio/questionnaires/{self.questionnaire.pk}/questions/new',
            'question',
            'question-form-error',
        )

    def test_member_specific_question_form_labels_help_and_prompt_error(self):
        self._assert_question_authoring_contract(
            f'/studio/questionnaires/{self.questionnaire.pk}/responses/{self.response.pk}/questions/new',
            'response-question',
            'response-question-form-error',
        )
