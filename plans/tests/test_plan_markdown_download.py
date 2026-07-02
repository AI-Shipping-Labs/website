"""Markdown download tests for sprint plans (issue #1108)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comments.models import Comment
from crm.models import (
    CRMRecord,
    IngestedProgressEvent,
    SlackChannelIngest,
    SlackMessage,
    SlackThread,
)
from plans.models import (
    NEXT_STEP_KIND_NEXT_STEP,
    NEXT_STEP_KIND_PRE_SPRINT,
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)
from questionnaires.models import Answer, Questionnaire, Response, ResponseQuestion

User = get_user_model()


def _member_download_url(plan):
    return reverse(
        'my_plan_markdown_download',
        kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
    )


def _studio_download_url(plan):
    return reverse('studio_plan_markdown_download', kwargs={'plan_id': plan.pk})


def _decode(response):
    return response.content.decode('utf-8')


class PlanMarkdownDownloadTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=4,
        )
        cls.owner = User.objects.create_user(
            email='owner@test.com',
            password='pw',
            first_name='Olive',
            last_name='Owner',
        )
        cls.teammate = User.objects.create_user(
            email='teammate@test.com',
            password='pw',
            first_name='Terry',
            last_name='Teammate',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.owner)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.teammate)
        cls.plan = Plan.objects.create(
            member=cls.owner,
            sprint=cls.sprint,
            visibility='cohort',
            title='Portable Sprint Plan',
            goal='Ship a **working** prototype',
            summary_current_situation='Prototype exists but is manual.',
            summary_goal='Automate the workflow.',
            summary_main_gap='Evaluation loop is missing.',
            summary_weekly_hours='6 hours / week',
            summary_why_this_plan='It creates a useful demo.',
            focus_main='Build the evaluator.',
            focus_supporting=['Add fixtures', 'Record a demo'],
            accountability='Post weekly progress.',
        )
        cls.week = Week.objects.create(
            plan=cls.plan,
            week_number=1,
            theme='Evaluation setup',
            position=0,
        )
        Checkpoint.objects.create(
            week=cls.week,
            description='Define success criteria',
            done_at=timezone.now(),
            position=0,
        )
        Checkpoint.objects.create(
            week=cls.week,
            description='Run baseline',
            position=1,
        )
        WeekNote.objects.create(
            week=cls.week,
            author=cls.owner,
            body='Participant note: baseline shipped.',
        )
        Resource.objects.create(
            plan=cls.plan,
            title='Eval guide',
            url='https://example.com/eval',
            note='Use **chapter 2**',
            position=0,
        )
        Deliverable.objects.create(
            plan=cls.plan,
            description='Demo recording',
            done_at=timezone.now(),
            position=0,
        )
        Deliverable.objects.create(
            plan=cls.plan,
            description='Writeup',
            position=1,
        )
        NextStep.objects.create(
            plan=cls.plan,
            kind=NEXT_STEP_KIND_PRE_SPRINT,
            description='Install dependencies',
            done_at=timezone.now(),
            position=0,
        )
        NextStep.objects.create(
            plan=cls.plan,
            kind=NEXT_STEP_KIND_NEXT_STEP,
            description='Book review call',
            position=1,
        )

    def test_owner_downloads_markdown_attachment(self):
        self.client.force_login(self.owner)

        response = self.client.get(_member_download_url(self.plan))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'text/markdown; charset=utf-8',
        )
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="sprint-plan-may-2026-{self.plan.pk}.md"',
        )
        self.assertNotIn('owner@test.com', response['Content-Disposition'])
        markdown = _decode(response)
        expected_sections = [
            '# Portable Sprint Plan',
            '- Sprint: May 2026',
            '- Member: Olive Owner',
            f'- Exported: {timezone.localdate().isoformat()}',
            '- Visibility: Shared with cohort',
            '- Progress: 1 of 2 checkpoints done',
            '## Goal',
            '## Summary',
            '### Current situation',
            '### Goal for this sprint',
            '### Main gap',
            '### Weekly time commitment',
            '### Why this plan',
            '## Focus',
            '## Timeline',
            '### Week 1: Evaluation setup',
            '- [x] Define success criteria',
            '- [ ] Run baseline',
            '#### Week notes',
            'Participant note: baseline shipped.',
            '## Resources',
            '- [Eval guide](https://example.com/eval) — Use **chapter 2**',
            '## Deliverables',
            '- [x] Demo recording',
            '- [ ] Writeup',
            '## Accountability',
            'Post weekly progress.',
            '## Pre-sprint actions',
            '- [x] Install dependencies',
            '## Next steps',
            '- [ ] Book review call',
        ]
        for text in expected_sections:
            self.assertIn(text, markdown)

        positions = [markdown.index(text) for text in [
            '## Goal',
            '## Summary',
            '## Focus',
            '## Timeline',
            '## Resources',
            '## Deliverables',
            '## Accountability',
            '## Pre-sprint actions',
            '## Next steps',
        ]]
        self.assertEqual(positions, sorted(positions))

    def test_empty_fields_and_collections_render_stable_placeholders(self):
        empty = Plan.objects.create(
            member=self.teammate,
            sprint=self.sprint,
            visibility='private',
        )
        self.client.force_login(self.teammate)

        response = self.client.get(_member_download_url(empty))

        self.assertEqual(response.status_code, 200)
        markdown = _decode(response)
        self.assertIn('## Goal\n_No goal yet._', markdown)
        self.assertIn('### Current situation\n_Not specified._', markdown)
        self.assertIn('## Focus\n_Not specified._', markdown)
        self.assertIn('- _No supporting focus items yet._', markdown)
        self.assertIn('## Timeline\n_No weeks yet._', markdown)
        self.assertIn('## Resources\n- _No resources yet._', markdown)
        self.assertIn('## Deliverables\n- _No deliverables yet._', markdown)
        self.assertIn('## Accountability\n_Not specified._', markdown)
        self.assertIn(
            '## Pre-sprint actions\n- _No pre-sprint actions yet._',
            markdown,
        )
        self.assertIn('## Next steps\n- _No next steps yet._', markdown)

    def test_internal_context_never_enters_owner_or_studio_export(self):
        InterviewNote.objects.create(
            member=self.owner,
            plan=self.plan,
            visibility='internal',
            body='INTERNAL_INTERVIEW_SECRET',
        )
        InterviewNote.objects.create(
            member=self.owner,
            plan=self.plan,
            visibility='external',
            body='EXTERNAL_INTERVIEW_NOTE',
        )
        CRMRecord.objects.create(
            user=self.owner,
            persona='CRM_PERSONA_SECRET',
            summary='CRM_SUMMARY_SECRET',
            next_steps='CRM_NEXT_STEPS_SECRET',
        )
        Comment.objects.create(
            content_id=self.plan.comment_content_id,
            user=self.staff,
            body='PLAN_COMMENT_SECRET',
        )
        questionnaire = Questionnaire.objects.create(
            title='Onboarding Leak Check',
            slug='onboarding-leak-check-1108',
            purpose='onboarding',
        )
        response = Response.objects.create(
            questionnaire=questionnaire,
            respondent=self.owner,
            status='submitted',
        )
        question = ResponseQuestion.objects.create(
            response=response,
            question_type='long_text',
            prompt='Internal onboarding prompt?',
            order=0,
        )
        Answer.objects.create(
            response=response,
            question=question,
            text_value='ONBOARDING_ANSWER_SECRET',
        )
        ingest = SlackChannelIngest.objects.create(
            channel_id='C123',
            status='success',
        )
        thread = SlackThread.objects.create(
            channel_id='C123',
            thread_ts='123.456',
            slack_user_id='U123',
            member=self.owner,
            plan=self.plan,
            posted_at=timezone.now(),
            ingest=ingest,
            last_seen_ingest=ingest,
        )
        SlackMessage.objects.create(
            thread=thread,
            ts='123.456',
            text='SLACK_MESSAGE_SECRET',
            posted_at=timezone.now(),
            is_root=True,
        )
        IngestedProgressEvent.objects.create(
            thread=thread,
            plan=self.plan,
            ingest=ingest,
            summary='SLACK_APPLY_SUMMARY_SECRET',
            blockers=['SLACK_BLOCKER_SECRET'],
            source_message_ts='123.456',
        )

        self.client.force_login(self.owner)
        owner_markdown = _decode(self.client.get(_member_download_url(self.plan)))
        self.client.force_login(self.staff)
        studio_markdown = _decode(self.client.get(_studio_download_url(self.plan)))

        for markdown in (owner_markdown, studio_markdown):
            self.assertIn('Participant note: baseline shipped.', markdown)
            self.assertNotIn('INTERNAL_INTERVIEW_SECRET', markdown)
            self.assertNotIn('EXTERNAL_INTERVIEW_NOTE', markdown)
            self.assertNotIn('CRM_PERSONA_SECRET', markdown)
            self.assertNotIn('CRM_SUMMARY_SECRET', markdown)
            self.assertNotIn('CRM_NEXT_STEPS_SECRET', markdown)
            self.assertNotIn('PLAN_COMMENT_SECRET', markdown)
            self.assertNotIn('ONBOARDING_ANSWER_SECRET', markdown)
            self.assertNotIn('SLACK_MESSAGE_SECRET', markdown)
            self.assertNotIn('SLACK_APPLY_SUMMARY_SECRET', markdown)
            self.assertNotIn('SLACK_BLOCKER_SECRET', markdown)

    def test_member_download_requires_owner_even_for_cohort_plan(self):
        self.client.force_login(self.teammate)

        response = self.client.get(_member_download_url(self.plan))

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('# Portable Sprint Plan', _decode(response))

    def test_anonymous_member_download_redirects_to_login(self):
        response = self.client.get(_member_download_url(self.plan))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_teammate_read_only_page_has_no_download_action(self):
        self.client.force_login(self.teammate)
        url = reverse(
            'member_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Build the evaluator')
        self.assertNotContains(response, 'Download Markdown')
        self.assertNotContains(response, 'download.md')

    def test_owner_workspace_shows_download_action(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse(
            'my_plan_detail',
            kwargs={'sprint_slug': self.sprint.slug, 'plan_id': self.plan.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Download Markdown')
        self.assertContains(response, 'data-testid="download-plan-markdown"')
        self.assertContains(response, _member_download_url(self.plan))

    def test_studio_staff_downloads_same_member_safe_markdown(self):
        self.client.force_login(self.staff)

        response = self.client.get(_studio_download_url(self.plan))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'text/markdown; charset=utf-8',
        )
        self.assertEqual(
            response['Content-Disposition'],
            f'attachment; filename="sprint-plan-may-2026-{self.plan.pk}.md"',
        )
        self.assertIn('# Portable Sprint Plan', _decode(response))

    def test_studio_download_is_staff_only(self):
        self.client.force_login(self.owner)

        response = self.client.get(_studio_download_url(self.plan))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn('# Portable Sprint Plan', _decode(response))

    def test_studio_detail_shows_download_action(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse(
            'studio_plan_detail',
            kwargs={'plan_id': self.plan.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Download Markdown')
        self.assertContains(response, 'data-testid="studio-plan-download-markdown"')
        self.assertContains(response, _studio_download_url(self.plan))
