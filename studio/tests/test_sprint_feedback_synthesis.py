"""Studio sprint feedback AI-synthesis surface (issue #805).

The LLM is mocked at the service boundary in every test (CI never hits a
live provider): either ``synthesize_feedback`` or ``llm.is_enabled`` /
``llm.complete``.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from integrations.services.feedback_synthesis import (
    FeedbackSynthesisResult,
    LLMError,
)
from plans.models import (
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
    SprintFeedbackSummary,
)
from plans.services import distribute_sprint_feedback
from questionnaires.models import Answer, Question, Questionnaire, Response

User = get_user_model()

_RESULT = FeedbackSynthesisResult(
    themes=[{'title': 'Pacing', 'summary': 'Too fast.', 'supporting_count': 2}],
    what_went_well=['Office hours helped.'],
    what_to_improve=['Slow week 2.'],
    recommendations=[{'recommendation': 'Add buffer.', 'rationale': 'Fell behind.'}],
    next_sprint_signal='Most plan to return.',
    response_count=3,
)


class _SynthMixin:
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), status='active',
        )
        cls.questionnaire = Questionnaire.objects.create(
            title='May Feedback', slug='may-fb', purpose='feedback',
        )
        cls.q = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='How did it go?', order=0,
        )
        cls.feedback_request = SprintFeedbackRequest.objects.create(
            sprint=cls.sprint, questionnaire=cls.questionnaire,
        )

    def _detail_url(self):
        return reverse('studio_sprint_detail', kwargs={'sprint_id': self.sprint.pk})

    def _synth_url(self):
        return reverse('studio_sprint_feedback_synthesize', kwargs={
            'sprint_id': self.sprint.pk,
            'feedback_request_id': self.feedback_request.pk,
        })

    def _enroll_and_submit(self, n):
        """Enroll n members, distribute, and submit a feedback answer each."""
        for i in range(n):
            user = User.objects.create_user(email=f'm{i}@test.com', password='pw')
            SprintEnrollment.objects.create(sprint=self.sprint, user=user)
        distribute_sprint_feedback(self.feedback_request)
        for response in Response.objects.filter(questionnaire=self.questionnaire):
            rq = response.response_questions.first()
            Answer.objects.create(
                response=response, question=rq, text_value='It went well.',
            )
            response.mark_submitted()


class SynthesizeViewTest(_SynthMixin, TestCase):
    def test_generates_and_upserts_single_summary(self):
        self._enroll_and_submit(3)
        self.client.force_login(self.staff)
        with patch(
            'studio.views.sprints.llm.is_enabled', return_value=True,
        ), patch(
            'studio.views.sprints.get_config', return_value='glm-5.1',
        ), patch(
            'studio.views.sprints.synthesize_feedback', return_value=_RESULT,
        ):
            response = self.client.post(self._synth_url())

        self.assertEqual(response.status_code, 302)
        summaries = SprintFeedbackSummary.objects.filter(
            feedback_request=self.feedback_request,
        )
        self.assertEqual(summaries.count(), 1)
        summary = summaries.get()
        self.assertEqual(summary.response_count, 3)
        self.assertEqual(summary.model_name, 'glm-5.1')
        self.assertEqual(summary.generated_by, self.staff)
        self.assertEqual(summary.result_json['themes'][0]['title'], 'Pacing')

    def test_regenerate_overwrites_not_duplicates(self):
        self._enroll_and_submit(3)
        self.client.force_login(self.staff)
        with patch(
            'studio.views.sprints.llm.is_enabled', return_value=True,
        ), patch(
            'studio.views.sprints.get_config', return_value='glm-5.1',
        ), patch(
            'studio.views.sprints.synthesize_feedback', return_value=_RESULT,
        ):
            self.client.post(self._synth_url())
            self.client.post(self._synth_url())

        self.assertEqual(
            SprintFeedbackSummary.objects.filter(
                feedback_request=self.feedback_request,
            ).count(),
            1,
        )

    def test_only_submitted_responses_are_synthesized(self):
        # Two submitted + one draft enrolled member; the assembled input
        # the callable receives must only carry the two submitted ones.
        self._enroll_and_submit(2)
        draft_user = User.objects.create_user(email='draft@test.com', password='pw')
        SprintEnrollment.objects.create(sprint=self.sprint, user=draft_user)
        distribute_sprint_feedback(self.feedback_request)  # creates draft response

        self.client.force_login(self.staff)
        captured = {}

        def _fake_synthesize(feedback):
            captured['count'] = feedback.response_count
            captured['responses'] = feedback.responses
            return _RESULT

        with patch(
            'studio.views.sprints.llm.is_enabled', return_value=True,
        ), patch(
            'studio.views.sprints.get_config', return_value='glm-5.1',
        ), patch(
            'studio.views.sprints.synthesize_feedback',
            side_effect=_fake_synthesize,
        ):
            self.client.post(self._synth_url())

        self.assertEqual(captured['count'], 2)
        self.assertEqual(len(captured['responses']), 2)

    def test_llm_error_redirects_with_error_and_writes_no_row(self):
        self._enroll_and_submit(3)
        self.client.force_login(self.staff)
        with patch(
            'studio.views.sprints.llm.is_enabled', return_value=True,
        ), patch(
            'studio.views.sprints.synthesize_feedback',
            side_effect=LLMError('boom'),
        ):
            response = self.client.post(self._synth_url(), follow=True)

        self.assertContains(response, 'the LLM request failed')
        self.assertFalse(
            SprintFeedbackSummary.objects.filter(
                feedback_request=self.feedback_request,
            ).exists()
        )

    def test_disabled_redirects_without_calling_callable(self):
        self._enroll_and_submit(3)
        self.client.force_login(self.staff)
        with patch(
            'studio.views.sprints.llm.is_enabled', return_value=False,
        ), patch(
            'studio.views.sprints.synthesize_feedback',
        ) as mock_synth:
            response = self.client.post(self._synth_url(), follow=True)

        mock_synth.assert_not_called()
        self.assertContains(response, 'AI synthesis is off')
        self.assertFalse(
            SprintFeedbackSummary.objects.filter(
                feedback_request=self.feedback_request,
            ).exists()
        )

    def test_no_submitted_responses_redirects_without_calling_callable(self):
        self.client.force_login(self.staff)
        with patch(
            'studio.views.sprints.llm.is_enabled', return_value=True,
        ), patch(
            'studio.views.sprints.synthesize_feedback',
        ) as mock_synth:
            response = self.client.post(self._synth_url(), follow=True)

        mock_synth.assert_not_called()
        self.assertContains(response, 'No submitted feedback')


class SynthesizeAccessControlTest(_SynthMixin, TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.post(self._synth_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)
        self.assertFalse(SprintFeedbackSummary.objects.exists())

    def test_non_staff_forbidden(self):
        self.client.force_login(self.non_staff)
        response = self.client.post(self._synth_url())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SprintFeedbackSummary.objects.exists())


class AISummarySubsectionTest(_SynthMixin, TestCase):
    def test_disabled_shows_settings_link_no_generate_button(self):
        self._enroll_and_submit(2)
        self.client.force_login(self.staff)
        with patch('studio.views.sprints.llm.is_enabled', return_value=False):
            page = self.client.get(self._detail_url())
        self.assertContains(page, 'data-testid="ai-summary-disabled"')
        self.assertContains(page, 'data-testid="ai-summary-settings-link"')
        self.assertNotContains(page, 'data-testid="ai-summary-generate-button"')

    def test_no_submitted_responses_shows_empty_no_button(self):
        self.client.force_login(self.staff)
        with patch('studio.views.sprints.llm.is_enabled', return_value=True):
            page = self.client.get(self._detail_url())
        self.assertContains(page, 'data-testid="ai-summary-empty"')
        self.assertNotContains(page, 'data-testid="ai-summary-generate-button"')

    def test_has_responses_no_summary_shows_generate_button(self):
        self._enroll_and_submit(2)
        self.client.force_login(self.staff)
        with patch('studio.views.sprints.llm.is_enabled', return_value=True):
            page = self.client.get(self._detail_url())
        self.assertContains(page, 'data-testid="ai-summary-generate-button"')

    def test_stored_summary_renders_content_and_provenance(self):
        self._enroll_and_submit(3)
        SprintFeedbackSummary.objects.create(
            feedback_request=self.feedback_request,
            result_json=_RESULT.model_dump(),
            response_count=3,
            model_name='glm-5.1',
            generated_by=self.staff,
            generated_at=datetime.datetime(2026, 5, 20, tzinfo=datetime.UTC),
        )
        self.client.force_login(self.staff)
        with patch('studio.views.sprints.llm.is_enabled', return_value=True):
            page = self.client.get(self._detail_url())

        self.assertContains(page, 'data-testid="ai-summary-result"')
        self.assertContains(page, 'data-testid="ai-summary-themes"')
        self.assertContains(page, 'Pacing')
        self.assertContains(page, 'data-testid="ai-summary-went-well"')
        self.assertContains(page, 'Office hours helped.')
        self.assertContains(page, 'data-testid="ai-summary-to-improve"')
        self.assertContains(page, 'data-testid="ai-summary-recommendations"')
        self.assertContains(page, 'Add buffer.')
        self.assertContains(page, 'data-testid="ai-summary-next-sprint-signal"')
        self.assertContains(page, 'Most plan to return.')
        self.assertContains(page, 'data-testid="ai-summary-provenance"')
        self.assertContains(page, 'glm-5.1')
        self.assertContains(page, 'staff@test.com')
        self.assertContains(page, 'data-testid="ai-summary-regenerate-button"')

    def test_stale_note_when_more_responses_than_stored(self):
        self._enroll_and_submit(3)
        SprintFeedbackSummary.objects.create(
            feedback_request=self.feedback_request,
            result_json=_RESULT.model_dump(),
            response_count=2,  # stored fewer than the 3 now submitted
            model_name='glm-5.1',
            generated_by=self.staff,
            generated_at=datetime.datetime(2026, 5, 20, tzinfo=datetime.UTC),
        )
        self.client.force_login(self.staff)
        with patch('studio.views.sprints.llm.is_enabled', return_value=True):
            page = self.client.get(self._detail_url())
        self.assertContains(page, 'data-testid="ai-summary-stale-note"')

    def test_no_stale_note_when_counts_match(self):
        self._enroll_and_submit(3)
        SprintFeedbackSummary.objects.create(
            feedback_request=self.feedback_request,
            result_json=_RESULT.model_dump(),
            response_count=3,
            model_name='glm-5.1',
            generated_by=self.staff,
            generated_at=datetime.datetime(2026, 5, 20, tzinfo=datetime.UTC),
        )
        self.client.force_login(self.staff)
        with patch('studio.views.sprints.llm.is_enabled', return_value=True):
            page = self.client.get(self._detail_url())
        self.assertNotContains(page, 'data-testid="ai-summary-stale-note"')
