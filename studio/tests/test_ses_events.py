"""Tests for the Studio SES events browser (issue #763).

Covers access control, list rendering, filter behaviour, pagination,
detail rendering, and sidebar navigation. The webhook side of
``email_app.SesEvent`` is exercised in ``api/tests/test_ses_events*``;
this module only tests the read-only Studio surfaces.

The model's ``received_at`` field uses ``auto_now_add=True`` so the
fixture helper writes specific timestamps via ``SesEvent.objects.filter
(pk=...).update(received_at=...)`` after the row is created. Updating
through the queryset bypasses ``auto_now_add`` (and ``save()``), which
is exactly what we want for deterministic ordering / date-range
assertions.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from freezegun import freeze_time

from accounts.models import Token
from email_app.models import EmailCampaign, EmailLog, SesEvent

User = get_user_model()


def _make_event(
    *,
    event_type,
    recipient_email='',
    user=None,
    email_log=None,
    bounce_type='',
    bounce_subtype='',
    diagnostic_code='',
    action_taken='',
    raw_payload=None,
    message_id=None,
    received_at=None,
    match_status='',
):
    """Create a ``SesEvent`` and optionally pin ``received_at``.

    ``message_id`` is auto-generated when not supplied — the field is
    ``unique=True`` so test fixtures need distinct values. Callers can
    still pin a specific value when they want to assert on it.
    """
    if message_id is None:
        message_id = f'msg-{SesEvent.objects.count() + 1}-{event_type}'
    if raw_payload is None:
        raw_payload = {'event_type': event_type, 'recipient': recipient_email}
    event = SesEvent.objects.create(
        event_type=event_type,
        recipient_email=recipient_email,
        user=user,
        email_log=email_log,
        bounce_type=bounce_type,
        bounce_subtype=bounce_subtype,
        diagnostic_code=diagnostic_code,
        action_taken=action_taken,
        raw_payload=raw_payload,
        message_id=message_id,
        match_status=match_status,
    )
    if received_at is not None:
        SesEvent.objects.filter(pk=event.pk).update(received_at=received_at)
        event.refresh_from_db()
    return event


class _SesEventFixtureMixin:
    """Six-row matrix from the issue spec, plus the staff / regular users."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        cls.user_a = User.objects.create_user(
            email='user_a@test.com', password='testpass',
        )
        cls.user_b = User.objects.create_user(
            email='user_b@test.com', password='testpass',
        )
        cls.user_c = User.objects.create_user(
            email='user_c@test.com', password='testpass',
        )

        cls.campaign = EmailCampaign.objects.create(
            subject='Weekly newsletter — issue 17',
            body='Body',
            status='sent',
        )
        cls.log_a = EmailLog.objects.create(
            campaign=cls.campaign,
            user=cls.user_a,
            email_type='campaign',
            ses_message_id='ses-aaa',
        )
        cls.log_b = EmailLog.objects.create(
            campaign=None,
            user=cls.user_c,
            email_type='welcome',
            ses_message_id='ses-bbb',
        )

        now = timezone.now()
        cls.now = now
        cls.row1 = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email='bouncer@example.com',
            user=cls.user_a,
            email_log=cls.log_a,
            bounce_type='Permanent',
            bounce_subtype='NoEmail',
            diagnostic_code='smtp; 550 5.1.1 user unknown',
            action_taken='unsubscribed and tagged bounced',
            received_at=now,
        )
        cls.row2 = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
            recipient_email='transient@example.com',
            bounce_type='Transient',
            bounce_subtype='General',
            received_at=now - datetime.timedelta(days=1),
        )
        cls.row3 = _make_event(
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
            recipient_email='complainer@example.com',
            user=cls.user_b,
            action_taken='unsubscribed',
            received_at=now - datetime.timedelta(days=2),
        )
        cls.row4 = _make_event(
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            recipient_email='delivered@example.com',
            user=cls.user_c,
            email_log=cls.log_b,
            received_at=now - datetime.timedelta(days=3),
        )
        cls.row5 = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email='another@example.com',
            bounce_type='Permanent',
            bounce_subtype='Suppressed',
            received_at=now - datetime.timedelta(days=30),
        )
        # Pin row6 to the very start of today rather than ``now - 1h`` so the
        # "received today" matrix stays on today's calendar date even when the
        # suite runs within the first hour after UTC midnight. With ``now - 1h``
        # the row rolled onto the previous day and ``test_since_filter`` flaked.
        cls.row6 = _make_event(
            event_type=SesEvent.EVENT_TYPE_SUBSCRIPTION_CONFIRMATION,
            recipient_email='',
            received_at=now.replace(hour=0, minute=0, second=1, microsecond=0),
        )


class SesEventListAccessTest(TestCase):
    """Staff-only access on ``/studio/ses-events/``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.regular = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )

    def test_anonymous_redirects_to_login(self):
        response = self.client.get('/studio/ses-events/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn('next=/studio/ses-events/', response.url)

    def test_non_staff_forbidden(self):
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get('/studio/ses-events/')
        self.assertEqual(response.status_code, 403)

    def test_staff_200(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/ses-events/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/ses_events/list.html')


class SesEventListRenderTest(_SesEventFixtureMixin, TestCase):
    """The list view renders every fixture row and links out correctly."""

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_all_recipients_visible(self):
        response = self.client.get('/studio/ses-events/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bouncer@example.com')
        self.assertContains(response, 'transient@example.com')
        self.assertContains(response, 'complainer@example.com')
        self.assertContains(response, 'delivered@example.com')
        self.assertContains(response, 'another@example.com')

    def test_rows_ordered_newest_first(self):
        response = self.client.get('/studio/ses-events/')
        body = response.content.decode()
        # row1 (now) appears before row5 (-30d) in the rendered HTML
        idx_row1 = body.find('bouncer@example.com')
        idx_row5 = body.find('another@example.com')
        self.assertGreater(idx_row1, -1)
        self.assertGreater(idx_row5, -1)
        self.assertLess(idx_row1, idx_row5)

    def test_bounce_permanent_carries_red_pill(self):
        response = self.client.get('/studio/ses-events/')
        # The pill class is attached to the rendered span; assert the
        # class is present alongside the bounce_permanent data-attribute.
        self.assertContains(
            response,
            'data-event-type="bounce_permanent"',
        )
        self.assertContains(response, 'bg-red-500/20 text-red-400')

    def test_user_link_rendered_for_matched_recipient(self):
        response = self.client.get('/studio/ses-events/')
        expected_href = f'/studio/users/{self.user_a.pk}/'
        self.assertContains(response, expected_href)

    def test_email_log_link_for_campaign_row(self):
        response = self.client.get('/studio/ses-events/')
        # Row 1's log is a campaign log; the cell should link to the
        # campaign detail page and show the email_type text.
        expected = f'/studio/campaigns/{self.campaign.pk}/'
        self.assertContains(response, expected)
        self.assertContains(response, 'campaign')  # email_type

    def test_action_taken_column_visible(self):
        response = self.client.get('/studio/ses-events/')
        self.assertContains(response, 'unsubscribed and tagged bounced')

    def test_empty_recipient_row_renders_without_crashing(self):
        # row6 has no recipient_email. It should still render — assert by
        # message_id being on the page (the row exists) and by the
        # subscription confirmation pill being visible.
        response = self.client.get('/studio/ses-events/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SubscriptionConfirmation')
        self.assertContains(response, f'ses-event-row-{self.row6.pk}')

    def test_select_related_keeps_queries_bounded(self):
        # The render path should use select_related for user / email_log /
        # campaign so we don't N+1 on a full page of rows. Loose upper bound
        # because session + auth + paginator count + page fetch all add to
        # the floor; the meaningful guarantee is that the query count does
        # not grow with the row count.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/studio/ses-events/')
        self.assertLessEqual(
            len(ctx.captured_queries), 15,
            f'Too many queries for a 6-row page: {len(ctx.captured_queries)}',
        )


class SesEventListEmptyTest(TestCase):
    """Empty-state behaviour when there are zero rows at all."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def test_fresh_empty_state_when_no_rows(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/ses-events/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'studio-empty-state-fresh')
        self.assertContains(response, 'Current results: 0')
        # Help copy below the empty card mentions the webhook path so
        # operators can diagnose the SNS subscription gap themselves.
        self.assertContains(response, '/api/ses-events')


class SesEventListFilterTest(_SesEventFixtureMixin, TestCase):
    """Query-param filter behaviour."""

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def _visible_pks(self, response):
        # Use the row data-testid to count visible rows in the rendered
        # table. The wrapper data-testid="studio-ses-events-list" remains
        # whether the table has rows or not.
        body = response.content.decode()
        return [
            event.pk for event in SesEvent.objects.all()
            if f'ses-event-row-{event.pk}' in body
        ]

    def test_q_filters_by_recipient_substring(self):
        response = self.client.get('/studio/ses-events/?q=bouncer')
        self.assertEqual(self._visible_pks(response), [self.row1.pk])

    def test_q_is_case_insensitive(self):
        response = self.client.get('/studio/ses-events/?q=BOUNCER')
        self.assertEqual(self._visible_pks(response), [self.row1.pk])

    def test_type_bounce_permanent_filter(self):
        response = self.client.get('/studio/ses-events/?type=bounce_permanent')
        visible = set(self._visible_pks(response))
        self.assertEqual(visible, {self.row1.pk, self.row5.pk})

    def test_type_complaint_filter(self):
        response = self.client.get('/studio/ses-events/?type=complaint')
        self.assertEqual(self._visible_pks(response), [self.row3.pk])

    def test_bounce_type_filter(self):
        response = self.client.get('/studio/ses-events/?bounce_type=Permanent')
        visible = set(self._visible_pks(response))
        self.assertEqual(visible, {self.row1.pk, self.row5.pk})

    def test_bounce_type_and_subtype_filter(self):
        response = self.client.get(
            '/studio/ses-events/?bounce_type=Permanent&bounce_subtype=NoEmail',
        )
        self.assertEqual(self._visible_pks(response), [self.row1.pk])

    def test_since_filter(self):
        today = self.now.date().isoformat()
        response = self.client.get(f'/studio/ses-events/?since={today}')
        visible = set(self._visible_pks(response))
        # Only events received today are kept (row1 + row6).
        self.assertEqual(visible, {self.row1.pk, self.row6.pk})

    def test_until_filter(self):
        # Two days ago — keeps row3 / row4 / row5 (and row2 if it's on
        # the boundary day depending on the test clock). Assert as
        # superset so the test is stable across midnight rollover.
        two_days_ago = (self.now - datetime.timedelta(days=2)).date().isoformat()
        response = self.client.get(f'/studio/ses-events/?until={two_days_ago}')
        visible = set(self._visible_pks(response))
        self.assertIn(self.row3.pk, visible)
        self.assertIn(self.row4.pk, visible)
        self.assertIn(self.row5.pk, visible)
        # row1 (today) should be filtered out.
        self.assertNotIn(self.row1.pk, visible)

    def test_combined_q_and_type(self):
        response = self.client.get(
            '/studio/ses-events/?q=example.com&type=bounce_permanent',
        )
        visible = set(self._visible_pks(response))
        self.assertEqual(visible, {self.row1.pk, self.row5.pk})

    def test_unknown_type_falls_through_to_all(self):
        response = self.client.get('/studio/ses-events/?type=garbage')
        visible = set(self._visible_pks(response))
        # All six rows visible.
        self.assertEqual(len(visible), 6)

    def test_invalid_since_silently_ignored(self):
        response = self.client.get('/studio/ses-events/?since=not-a-date')
        self.assertEqual(response.status_code, 200)
        visible = set(self._visible_pks(response))
        self.assertEqual(len(visible), 6)

    def test_other_type_bucket(self):
        response = self.client.get('/studio/ses-events/?type=other')
        # row6 (subscription_confirmation) lands in the Other bucket.
        self.assertEqual(self._visible_pks(response), [self.row6.pk])

    def test_filter_zero_rows_shows_filter_empty_state(self):
        response = self.client.get('/studio/ses-events/?q=zzzznever')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'studio-empty-state-filter')

    def test_filter_zero_rows_clear_link_present(self):
        response = self.client.get('/studio/ses-events/?q=zzzznever')
        self.assertContains(response, 'Clear filters')


class SesEventListPagerTest(TestCase):
    """Page size + query-param preservation across navigation."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # 60 rows of the same type so the type filter is exercised on
        # the pager link as well.
        now = timezone.now()
        for i in range(60):
            _make_event(
                event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
                recipient_email=f'bulk-{i}@example.com',
                bounce_type='Permanent',
                bounce_subtype='General',
                message_id=f'msg-bulk-{i}',
                received_at=now - datetime.timedelta(minutes=i),
            )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_first_page_shows_50_rows(self):
        response = self.client.get('/studio/ses-events/')
        body = response.content.decode()
        row_count = body.count('data-testid="ses-event-row-')
        self.assertEqual(row_count, 50)

    def test_second_page_shows_remaining_10_rows(self):
        response = self.client.get('/studio/ses-events/?page=2')
        body = response.content.decode()
        row_count = body.count('data-testid="ses-event-row-')
        self.assertEqual(row_count, 10)

    def test_out_of_range_page_clamps_to_last(self):
        response = self.client.get('/studio/ses-events/?page=999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 2)

    def test_pager_preserves_query_params(self):
        response = self.client.get(
            '/studio/ses-events/?q=bulk&type=bounce_permanent',
        )
        body = response.content.decode()
        # The "next" link should carry q + type + page=2.
        self.assertIn('page=2', body)
        self.assertIn('q=bulk', body)
        self.assertIn('type=bounce_permanent', body)

    def test_pager_preserves_bounce_filters(self):
        response = self.client.get(
            '/studio/ses-events/'
            '?q=bulk&type=bounce_permanent'
            '&bounce_type=Permanent&bounce_subtype=General',
        )
        next_url = response.context['pager_next_url']
        self.assertIn('q=bulk', next_url)
        self.assertIn('type=bounce_permanent', next_url)
        self.assertIn('bounce_type=Permanent', next_url)
        self.assertIn('bounce_subtype=General', next_url)
        self.assertIn('page=2', next_url)


class SesEventDetailAccessTest(_SesEventFixtureMixin, TestCase):
    """Access control + 404 behaviour on the detail view."""

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_forbidden(self):
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_staff_existing_pk_200(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/ses_events/detail.html')

    def test_staff_missing_pk_404(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get('/studio/ses-events/99999999/')
        self.assertEqual(response.status_code, 404)


class SesEventDetailRenderTest(_SesEventFixtureMixin, TestCase):
    """Detail view renders payload + matched objects + back link."""

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_payload_rendered_as_pretty_json(self):
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        body = response.content.decode()
        # ``json.dumps(..., indent=2)`` introduces newlines + two-space
        # indents around the payload keys. The template auto-escapes the
        # double quotes in the ``<pre>`` block, so we assert on the
        # escaped form.
        self.assertIn(
            '  &quot;event_type&quot;: &quot;bounce_permanent&quot;', body,
        )
        # Confirm the indent + newline structure is preserved (vs. the
        # compact ``json.dumps`` default with no spaces).
        self.assertIn('\n  ', body)

    def test_user_link_when_user_set(self):
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        self.assertContains(response, f'/studio/users/{self.user_a.pk}/')

    def test_unmatched_user_has_explicit_identity_status(self):
        response = self.client.get(f'/studio/ses-events/{self.row2.pk}/')
        self.assertContains(response, 'No platform account')
        self.assertContains(response, 'No account link')

    def test_email_log_section_when_set(self):
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        self.assertContains(response, 'ses-event-detail-email-log')
        # ses_message_id of the matched log is rendered in mono.
        self.assertContains(response, 'ses-aaa')

    def test_no_matched_send_literal_when_email_log_null(self):
        response = self.client.get(f'/studio/ses-events/{self.row2.pk}/')
        self.assertContains(response, 'No matched send')

    def test_message_id_rendered_in_mono(self):
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        body = response.content.decode()
        # The dedicated message-id cell carries ``font-mono``.
        self.assertIn('ses-event-detail-message-id', body)
        # Find the message-id testid block and confirm font-mono is on
        # the same element.
        marker = 'data-testid="ses-event-detail-message-id"'
        idx = body.find(marker)
        self.assertGreater(idx, -1)
        snippet = body[max(idx - 200, 0):idx + 200]
        self.assertIn('font-mono', snippet)

    def test_back_link_to_list(self):
        response = self.client.get(f'/studio/ses-events/{self.row1.pk}/')
        self.assertContains(response, '/studio/ses-events/')
        self.assertContains(response, 'Back to SES events')


class SesEventSidebarLinkTest(_SesEventFixtureMixin, TestCase):
    """The sidebar entry shows up under Operations and lights up."""

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_sidebar_contains_ses_events_link(self):
        response = self.client.get('/studio/')
        # The sidebar link uses url='studio_ses_event_list' which resolves
        # to /studio/ses-events/.
        self.assertContains(response, 'href="/studio/ses-events/"')
        self.assertContains(response, '>SES events<')

    def test_active_state_when_on_ses_events_page(self):
        response = self.client.get('/studio/ses-events/')
        body = response.content.decode()
        # Find the sidebar link's <a> tag and confirm it carries the
        # active bg-secondary class. The body contains multiple
        # ``bg-secondary`` occurrences so we anchor on the link's href.
        link_marker = 'href="/studio/ses-events/"'
        idx = body.find(link_marker)
        self.assertGreater(idx, -1)
        # Sidebar link is rendered with class string immediately after the
        # href attribute; the active branch carries ``bg-secondary text-foreground``.
        snippet = body[idx:idx + 400]
        self.assertIn('bg-secondary text-foreground', snippet)


@freeze_time('2026-08-31 12:00:00')
class SesEventSummaryWindowTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='summary-staff@example.com', password='testpass', is_staff=True,
        )
        cls.campaign = EmailCampaign.objects.create(
            subject='Narrow campaign', body='Body', status='sent',
        )
        start = datetime.datetime(2026, 8, 2, tzinfo=datetime.UTC)
        cls.at_boundary = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email='inside@example.com',
            message_id='summary-inside',
            received_at=start,
        )
        cls.before_boundary = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email='outside@example.com',
            message_id='summary-outside',
            received_at=start - datetime.timedelta(microseconds=1),
        )
        cls.complaint = _make_event(
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
            recipient_email='complaint@example.com',
            message_id='summary-complaint',
            received_at=start + datetime.timedelta(days=1),
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='testpass')

    def test_cards_share_utc_boundary_and_link_to_matching_totals(self):
        response = self.client.get('/studio/ses-events/')

        self.assertEqual(response.context['summary_window_start'], '2026-08-02')
        self.assertEqual(response.context['last_30d_bounce_permanent'], 1)
        self.assertEqual(response.context['last_30d_complaint'], 1)
        self.assertEqual(response.context['last_30d_total'], 2)
        self.assertContains(response, 'Permanent bounces (30d)')
        self.assertContains(response, 'All SES activity · last 30 UTC calendar days')

        bounce_response = self.client.get(response.context['summary_links']['bounce'])
        self.assertEqual(bounce_response.context['filtered_total'], 1)
        self.assertEqual(bounce_response.context['type_filter'], 'bounce_permanent')
        self.assertEqual(bounce_response.context['since'], '2026-08-02')

    def test_card_links_clear_narrower_context(self):
        response = self.client.get(
            '/studio/ses-events/',
            {
                'campaign': self.campaign.pk,
                'q': 'unsafe raw text',
                'bounce_type': 'Permanent',
                'bounce_subtype': 'General',
                'until': '2026-08-31',
                'page': '2',
            },
        )

        bounce_url = response.context['summary_links']['bounce']
        self.assertEqual(
            bounce_url,
            '/studio/ses-events/?type=bounce_permanent&since=2026-08-02',
        )
        self.assertNotIn('campaign=', bounce_url)
        self.assertNotIn('q=', bounce_url)


class SesEventCampaignContextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='campaign-staff@example.com', password='testpass', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='ses-parity')
        cls.campaign = EmailCampaign.objects.create(
            subject='Campaign with feedback', body='Body', status='sent',
        )
        cls.empty_campaign = EmailCampaign.objects.create(
            subject='Campaign without feedback', body='Body', status='sent',
        )
        user = User.objects.create_user(email='campaign-recipient@example.com')
        log = EmailLog.objects.create(
            campaign=cls.campaign,
            user=user,
            recipient_email=user.email,
            email_type='campaign',
            ses_message_id='campaign-linked-send',
        )
        cls.linked_event = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email=user.email,
            user=user,
            email_log=log,
            match_status=SesEvent.MATCH_STATUS_PRIMARY_EMAIL,
            message_id='campaign-linked-event',
        )
        cls.unrelated = _make_event(
            event_type=SesEvent.EVENT_TYPE_COMPLAINT,
            recipient_email='private-recipient@example.net',
            diagnostic_code='private diagnostic',
            message_id='campaign-unrelated-event',
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='testpass')

    def test_invalid_nonempty_campaign_context_fails_closed(self):
        cases = ['not-a-valid-id', '0', '-1', '999999']
        for value in cases:
            with self.subTest(campaign=value):
                response = self.client.get(
                    '/studio/ses-events/', {'campaign': value},
                )
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(
                    response,
                    'private-recipient@example.net',
                    status_code=404,
                )
                self.assertNotContains(
                    response, 'private diagnostic', status_code=404,
                )

    def test_empty_campaign_parameter_is_unfiltered(self):
        response = self.client.get('/studio/ses-events/?campaign=')
        self.assertEqual(response.context['filtered_total'], 2)
        self.assertIsNone(response.context['campaign'])

    def test_campaign_without_linked_events_explains_global_mismatch(self):
        response = self.client.get(
            '/studio/ses-events/', {'campaign': self.empty_campaign.pk},
        )

        self.assertEqual(response.context['filtered_total'], 0)
        self.assertContains(
            response,
            'No SES events are linked to “Campaign without feedback”.',
        )
        self.assertContains(response, 'all campaigns and transactional email')
        self.assertContains(response, 'View all SES events')
        self.assertContains(
            response,
            reverse('studio_campaign_recipients', args=[self.empty_campaign.pk]),
        )

    def test_campaign_linked_events_hidden_by_filter_preserves_campaign(self):
        response = self.client.get(
            '/studio/ses-events/',
            {'campaign': self.campaign.pk, 'type': 'complaint'},
        )

        self.assertEqual(response.context['filtered_total'], 0)
        self.assertContains(
            response,
            'has SES events, but none match the remaining filters',
        )
        self.assertContains(response, 'Clear event filters')
        self.assertEqual(
            response.context['empty_recovery_url'],
            f'/studio/ses-events/?campaign={self.campaign.pk}',
        )

    def test_campaign_total_and_scope_use_full_filtered_queryset(self):
        response = self.client.get(
            '/studio/ses-events/', {'campaign': self.campaign.pk},
        )

        self.assertEqual(response.context['filtered_total'], 1)
        self.assertEqual(response.context['paginator'].count, 1)
        self.assertContains(response, 'Current results: 1')
        self.assertContains(response, 'campaign/active-filter scope')

        api_response = self.client.get(
            '/api/ses-events',
            {'campaign': self.campaign.pk},
            HTTP_AUTHORIZATION=f'Token {self.staff_token.key}',
        )
        api_body = api_response.json()
        self.assertEqual(
            api_body['count'], response.context['paginator'].count,
        )


class SesEventHistoricalIdentityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='history-staff@example.com', password='testpass', is_staff=True,
        )
        cls.user = User.objects.create_user(email='history-user@example.com')
        cls.event = _make_event(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
            recipient_email='History User <history-user@example.com>',
            user=cls.user,
            action_taken='History User <history-user@example.com>: no matching user',
            message_id='historical-contradiction',
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='testpass')

    def test_contradictory_historical_row_is_flagged_without_replay(self):
        original_count = self.user.soft_bounce_count
        response = self.client.get(
            reverse('studio_ses_event_detail', args=[self.event.pk]),
        )

        self.assertContains(response, 'Matched account · Needs reconciliation')
        self.assertContains(response, 'Needs reconciliation')
        self.assertContains(response, 'Historical webhook action:')
        self.assertContains(response, reverse('studio_user_detail', args=[self.user.pk]))
        self.user.refresh_from_db()
        self.assertEqual(self.user.soft_bounce_count, original_count)


class SesEventAdminRemovalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email='ses-admin@example.com', password='testpass',
        )

    def test_obsolete_admin_routes_are_absent_without_redirect(self):
        with self.assertRaises(NoReverseMatch):
            reverse('admin:email_app_sesevent_changelist')

        self.client.force_login(self.superuser)
        for path in (
            '/admin/email_app/sesevent/',
            '/admin/email_app/sesevent/1/change/',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response.headers)
