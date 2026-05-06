"""Studio drag-and-drop plan editor (issue #434).

These tests cover the thin server shell only:

- Access control matrix (anonymous / non-staff / staff).
- The bootstrap JSON payload matches the API detail shape from #433.
- The editor renders summary fields, focus block, week cards, side
  panels, the SortableJS CDN with SRI, and a per-staff API token via
  ``data-api-token`` (never inlined in a ``<script>`` source).
- Re-opening the editor reuses the same token rather than minting a new
  one each time -- the editor token is a singleton per staff user.

Drag, keyboard, autosave, revert-on-failure and toast behaviour are
JavaScript flows; per testing-guidelines Rule 4 they belong in
Playwright (see ``playwright_tests/test_studio_plan_editor.py``), not
in Django ``TestCase``.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
)

User = get_user_model()


class PlanEditorAccessControlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def test_anonymous_redirects_to_login(self):
        url = f'/studio/plans/{self.plan.pk}/edit/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertIn(f'next={url}', response['Location'])

    def test_non_staff_returns_403(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 403)

    def test_non_staff_does_not_see_editor_markup_or_token(self):
        """Even on the 403 response, no SortableJS or token leaks.

        Rule 1: assert specifically that the failure mode does not
        accidentally serve the editor (a future regression where the
        decorator stops before the template render but still includes
        partial markup would be silently dangerous).
        """
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        body = response.content.decode()
        self.assertNotIn('sortablejs', body.lower())
        self.assertNotIn('plan-editor-data', body)
        # The non-staff branch must not mint a token.
        self.assertEqual(
            Token.objects.filter(name='studio-plan-editor').count(),
            0,
        )

    def test_staff_returns_200(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)

    def test_unknown_plan_returns_404(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/plans/9999999/edit/')
        self.assertEqual(response.status_code, 404)


class PlanEditorBootstrapPayloadTest(TestCase):
    """The plan-editor data script holds the API detail shape from #433."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='carlos@example.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026 sprint', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), duration_weeks=6,
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint, status='shared',
            summary_current_situation='SITN',
            summary_goal='GOAL',
            summary_main_gap='GAP',
            summary_weekly_hours='10 hrs',
            summary_why_this_plan='WHY',
            focus_main='Ship one project',
            focus_supporting=['Brand', 'Network'],
            accountability='Weekly check-in',
        )
        cls.week_1 = Week.objects.create(
            plan=cls.plan, week_number=1, theme='Foundations', position=0,
        )
        cls.week_2 = Week.objects.create(
            plan=cls.plan, week_number=2, theme='Build', position=1,
        )
        cls.cp_a = Checkpoint.objects.create(
            week=cls.week_1, description='Read paper', position=0,
        )
        cls.cp_b = Checkpoint.objects.create(
            week=cls.week_1, description='Build prototype', position=1,
        )
        cls.cp_c = Checkpoint.objects.create(
            week=cls.week_2, description='Write blog post', position=0,
            done_at=timezone.now(),
        )
        cls.resource = Resource.objects.create(
            plan=cls.plan, title='LinkedIn guide', url='https://example.com/li',
            note='Bookmark', position=0,
        )
        cls.deliverable = Deliverable.objects.create(
            plan=cls.plan, description='Working prototype', position=0,
        )
        cls.next_step = NextStep.objects.create(
            plan=cls.plan, assignee_label='Alexey',
            description='Schedule check-in', position=0,
        )
        cls.internal_note = InterviewNote.objects.create(
            plan=cls.plan, member=cls.member,
            visibility='internal', kind='meeting',
            body='INTERNAL_NOTE_BODY',
        )
        cls.external_note = InterviewNote.objects.create(
            plan=cls.plan, member=cls.member,
            visibility='external', kind='general',
            body='EXTERNAL_NOTE_BODY',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.response = response
        self.payload = self._extract_payload(response.content.decode())

    @staticmethod
    def _extract_payload(html):
        marker = '<script id="plan-editor-data"'
        start = html.index(marker)
        gt = html.index('>', start) + 1
        end = html.index('</script>', gt)
        raw = html[gt:end]
        return json.loads(raw)

    def test_payload_top_level_shape(self):
        self.assertEqual(self.payload['id'], self.plan.pk)
        self.assertEqual(self.payload['sprint'], 'may-2026')
        self.assertEqual(self.payload['user_email'], 'carlos@example.com')
        self.assertEqual(self.payload['status'], 'shared')
        self.assertEqual(self.payload['duration_weeks'], 6)

    def test_payload_summary_block(self):
        summary = self.payload['summary']
        self.assertEqual(summary['current_situation'], 'SITN')
        self.assertEqual(summary['goal'], 'GOAL')
        self.assertEqual(summary['main_gap'], 'GAP')
        self.assertEqual(summary['weekly_hours'], '10 hrs')
        self.assertEqual(summary['why_this_plan'], 'WHY')

    def test_payload_focus_block(self):
        focus = self.payload['focus']
        self.assertEqual(focus['main'], 'Ship one project')
        self.assertEqual(focus['supporting'], ['Brand', 'Network'])

    def test_payload_weeks_in_position_order(self):
        weeks = self.payload['weeks']
        self.assertEqual([w['week_number'] for w in weeks], [1, 2])
        self.assertEqual(weeks[0]['theme'], 'Foundations')
        self.assertEqual(weeks[1]['theme'], 'Build')

    def test_payload_checkpoints_in_position_order_per_week(self):
        weeks = self.payload['weeks']
        descriptions_w1 = [c['description'] for c in weeks[0]['checkpoints']]
        self.assertEqual(descriptions_w1, ['Read paper', 'Build prototype'])
        descriptions_w2 = [c['description'] for c in weeks[1]['checkpoints']]
        self.assertEqual(descriptions_w2, ['Write blog post'])

    def test_payload_done_at_is_serialized(self):
        weeks = self.payload['weeks']
        cp_a = weeks[0]['checkpoints'][0]
        self.assertIsNone(cp_a['done_at'])
        cp_c = weeks[1]['checkpoints'][0]
        self.assertIsNotNone(cp_c['done_at'])

    def test_payload_includes_resource_deliverable_next_step(self):
        self.assertEqual(len(self.payload['resources']), 1)
        self.assertEqual(self.payload['resources'][0]['title'], 'LinkedIn guide')
        self.assertEqual(len(self.payload['deliverables']), 1)
        self.assertEqual(
            self.payload['deliverables'][0]['description'],
            'Working prototype',
        )
        self.assertEqual(len(self.payload['next_steps']), 1)
        self.assertEqual(
            self.payload['next_steps'][0]['assignee_label'],
            'Alexey',
        )

    def test_payload_splits_interview_notes_by_visibility(self):
        notes = self.payload['interview_notes']
        internal_bodies = [n['body'] for n in notes['internal']]
        external_bodies = [n['body'] for n in notes['external']]
        # Each note appears in exactly one bucket.
        self.assertIn('INTERNAL_NOTE_BODY', internal_bodies)
        self.assertNotIn('INTERNAL_NOTE_BODY', external_bodies)
        self.assertIn('EXTERNAL_NOTE_BODY', external_bodies)
        self.assertNotIn('EXTERNAL_NOTE_BODY', internal_bodies)


class PlanEditorRenderTest(TestCase):
    """The editor shell renders the documented surface elements."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026 sprint', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint, status='draft',
        )
        Week.objects.create(plan=cls.plan, week_number=1, position=0)
        Week.objects.create(plan=cls.plan, week_number=2, position=1)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_header_renders_member_email_and_sprint(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(
            response,
            f'href="/studio/users/{self.member.pk}/"',
        )
        self.assertContains(response, 'May 2026 sprint')
        self.assertContains(response, 'may-2026')

    def test_header_renders_status_pill_with_data_attribute(self):
        """Pill renders the human label and a data attribute the JS reads.

        The JS toggles the pill via ``data-current-status`` rather than
        scraping the visible text, so the data attribute must be present
        and match the plan's status. Asserting on both means the
        rendering matches the JS contract.
        """
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'data-current-status="draft"')
        self.assertContains(response, 'data-testid="plan-status-pill"')
        self.assertContains(response, 'Draft')

    def test_header_renders_not_yet_shared_when_unset(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'Not yet shared')

    def test_header_renders_shared_at_when_set(self):
        self.plan.shared_at = timezone.now()
        self.plan.save(update_fields=['shared_at'])
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        # ``Not yet shared`` is the falsy branch -- it must NOT render
        # when the timestamp is set, otherwise both branches would
        # render and the pill would be ambiguous (Rule 1 negative).
        self.assertNotContains(response, 'Not yet shared')

    def test_summary_textareas_are_rendered_with_field_names(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        for field in [
            'summary_current_situation',
            'summary_goal',
            'summary_main_gap',
            'summary_weekly_hours',
            'summary_why_this_plan',
        ]:
            self.assertContains(response, f'data-field="{field}"', msg_prefix=field)

    def test_focus_main_textarea_is_rendered(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'data-field="focus_main"')

    def test_one_week_card_per_week_in_position_order(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        # Each week card is rendered with a data-week-number attribute.
        self.assertContains(response, 'data-week-number="1"')
        self.assertContains(response, 'data-week-number="2"')
        # No phantom Week 3 placeholder.
        self.assertNotContains(response, 'data-week-number="3"')

    def test_side_panels_are_present(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'data-testid="resources-panel"')
        self.assertContains(response, 'data-testid="deliverables-panel"')
        self.assertContains(response, 'data-testid="next-steps-panel"')
        self.assertContains(response, 'data-testid="interview-notes-panel"')
        # Both visibility tabs render.
        self.assertContains(response, 'data-testid="interview-notes-tab-internal"')
        self.assertContains(response, 'data-testid="interview-notes-tab-external"')

    def test_sortable_js_loaded_with_pinned_version_and_sri(self):
        """SortableJS comes from cdn.jsdelivr.net at version 1.15.2 with SRI.

        AC says the pin must NOT use ``latest`` and must include an
        ``integrity`` attribute. Asserting on the exact URL guarantees
        regressions are caught.
        """
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(
            response,
            'https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js',
        )
        self.assertContains(response, 'integrity="sha384-')
        self.assertContains(response, 'crossorigin="anonymous"')
        self.assertNotContains(response, 'sortablejs@latest')

    def test_save_indicator_present_with_initial_saved_state(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'data-testid="save-indicator"')
        self.assertContains(response, 'data-state="saved"')

    def test_activity_line_counts_checkpoints_and_weeks(self):
        Checkpoint.objects.create(
            week=Week.objects.get(plan=self.plan, week_number=1),
            description='cp1', position=0,
        )
        Checkpoint.objects.create(
            week=Week.objects.get(plan=self.plan, week_number=2),
            description='cp2', position=0,
        )
        Checkpoint.objects.create(
            week=Week.objects.get(plan=self.plan, week_number=2),
            description='cp3', position=1,
        )
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, '3 checkpoints across 2 weeks')


class PlanEditorTokenTest(TestCase):
    """The editor mints (or reuses) a single API token per staff user."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_token_attached_via_data_api_token_attribute(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        token = Token.objects.get(user=self.staff, name='studio-plan-editor')
        self.assertContains(response, f'data-api-token="{token.key}"')

    def test_token_not_inlined_in_script_source(self):
        """The token must live on a data attribute, not a <script> body.

        AC: ``data-api-token`` on the editor root, never inline in
        ``<script>`` source. Reading the page and asserting the token
        does NOT appear inside any <script> tag's text guards against
        a future change that copies it into a JS variable initialiser.
        Django strips ``{# ... #}`` comments so the only ``<script``
        opens left in the response are real tags. The bootstrap data
        node is excluded from the check because it carries the
        plan JSON, not JS source -- the API token must not leak there
        either, but the JSON parse step verifies the payload separately.
        """
        from html.parser import HTMLParser

        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        token = Token.objects.get(user=self.staff, name='studio-plan-editor')
        body = response.content.decode()

        class ScriptCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.buffers = []
                self._capture = False
                self._current = []
                self._is_data_node = False

            def handle_starttag(self, tag, attrs):
                if tag == 'script':
                    attr_dict = dict(attrs)
                    self._is_data_node = (
                        attr_dict.get('id') == 'plan-editor-data'
                    )
                    self._capture = True
                    self._current = []

            def handle_endtag(self, tag):
                if tag == 'script' and self._capture:
                    self.buffers.append({
                        'is_data_node': self._is_data_node,
                        'text': ''.join(self._current),
                    })
                    self._capture = False
                    self._current = []
                    self._is_data_node = False

            def handle_data(self, data):
                if self._capture:
                    self._current.append(data)

        collector = ScriptCollector()
        collector.feed(body)
        # There must be at least one <script> tag in the output --
        # otherwise this assertion is vacuous (Rule 1 self-check).
        self.assertGreater(len(collector.buffers), 0)
        for entry in collector.buffers:
            if entry['is_data_node']:
                # The bootstrap JSON is a separate concern; verify the
                # token is not embedded in it either.
                self.assertNotIn(
                    token.key,
                    entry['text'],
                    msg='API token leaked into the bootstrap JSON',
                )
                continue
            self.assertNotIn(
                token.key,
                entry['text'],
                msg='API token leaked into a <script> block',
            )

    def test_api_base_attribute_present(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'data-api-base="/api/"')

    def test_token_is_reused_across_page_loads(self):
        """The editor MUST get-or-create -- never mint a fresh token per page.

        Reloading the editor a thousand times in a session should yield
        exactly one token row for the user, with the same key.
        """
        before = Token.objects.filter(
            user=self.staff, name='studio-plan-editor',
        ).count()
        self.assertEqual(before, 0)

        first = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        second = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        third = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 200)

        tokens = Token.objects.filter(
            user=self.staff, name='studio-plan-editor',
        )
        self.assertEqual(tokens.count(), 1)
        token = tokens.get()
        for response in [first, second, third]:
            self.assertContains(response, f'data-api-token="{token.key}"')


class PlanEditorSidebarTest(TestCase):
    """The Members sidebar entries highlight on the editor URL.

    The orchestrator note says the spec calls for a new ``Community``
    section but #432 already added a ``Members`` section with both
    Sprints and Plans entries. This test confirms the existing Members
    section's ``/plans`` substring match also catches the new editor
    URL, so no second sidebar entry is necessary.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(member=cls.member, sprint=cls.sprint)

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_plans_sidebar_entry_highlighted_on_editor(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        # The Plans sidebar link's active state is the ``bg-secondary
        # text-foreground`` class pair; assert that the link with the
        # ``/studio/plans/`` href carries those classes when we are on
        # the editor URL.
        body = response.content.decode()
        plans_link_idx = body.find('href="/studio/plans/"')
        self.assertNotEqual(plans_link_idx, -1)
        # The class attribute lives within ~200 chars before the href.
        snippet = body[max(0, plans_link_idx - 300):plans_link_idx + 200]
        self.assertIn('bg-secondary', snippet)
        self.assertIn('text-foreground', snippet)
