import datetime
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import EmailAlias
from email_app.models import EmailCampaign, EmailLog, SesEvent

User = get_user_model()

REPO_ROOT = Path(__file__).resolve().parents[2]
FOCUS_RING_TOKENS = {
    "focus-visible:outline-none",
    "focus-visible:ring-2",
    "focus-visible:ring-accent",
    "focus-visible:ring-offset-2",
    "focus-visible:ring-offset-background",
}


def _interactive_tags(source):
    return re.findall(r"<(?:a|button)\b[^>]*>", source, flags=re.DOTALL)


class EmailLogFocusContractTest(SimpleTestCase):
    """Issue-owned hover controls must retain the Studio keyboard ring."""

    def test_all_ten_issue_owned_hover_controls_have_focus_visible_ring(self):
        base = (REPO_ROOT / "templates/studio/base.html").read_text()
        email_log = (
            REPO_ROOT / "templates/studio/email_log/list.html"
        ).read_text()
        user_detail = (
            REPO_ROOT / "templates/studio/users/detail.html"
        ).read_text()
        ses_events = (
            REPO_ROOT / "templates/studio/ses_events/list.html"
        ).read_text()

        controls = [
            tag for tag in _interactive_tags(base)
            if "studio_email_log_list" in tag
        ]
        controls.extend(
            tag for tag in _interactive_tags(email_log) if "hover:" in tag
        )

        history_start = user_detail.index(
            'data-testid="user-email-history-section"'
        )
        history_end = user_detail.index(
            'data-testid="user-aliases-section"', history_start,
        )
        controls.extend(
            tag for tag in _interactive_tags(
                user_detail[history_start:history_end]
            )
            if "hover:" in tag
        )

        identity_start = ses_events.index(
            'data-testid="ses-event-user-filter"'
        )
        identity_end = ses_events.index("</div>", identity_start)
        controls.extend(
            tag for tag in _interactive_tags(
                ses_events[identity_start:identity_end]
            )
            if "hover:" in tag
        )

        self.assertEqual(
            len(controls), 10,
            "update the #1283 focus inventory when controls are added/removed",
        )
        offenders = []
        for tag in controls:
            missing = sorted(
                token for token in FOCUS_RING_TOKENS if token not in tag
            )
            if missing:
                offenders.append({"tag": " ".join(tag.split()), "missing": missing})
        self.assertEqual(offenders, [])


class StudioEmailLogTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-email-log@example.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@example.com", password="pw",
        )
        cls.other = User.objects.create_user(email="other@example.com", password="pw")
        EmailAlias.objects.create(user=cls.member, email="member-old@example.com")
        cls.campaign = EmailCampaign.objects.create(subject="July briefing", body="Body")
        cls.primary = EmailLog.objects.create(
            user=cls.member,
            recipient_email="Member@Example.com",
            email_type="welcome",
            subject="Welcome aboard",
            ses_message_id="ses-primary",
        )
        cls.alias = EmailLog.objects.create(
            recipient_email="MEMBER-OLD@example.com",
            email_type="campaign",
            campaign=cls.campaign,
            subject="July briefing",
            ses_message_id="ses-alias",
        )
        cls.owned_old = EmailLog.objects.create(
            user=cls.member,
            recipient_email="historic@elsewhere.example",
            email_type="account_notice",
            subject="",
            ses_message_id="ses-owned-old",
        )
        cls.external = EmailLog.objects.create(
            recipient_email="external@example.net",
            email_type="external_notice",
            subject="",
            ses_message_id="ses-external",
        )
        cls.other_log = EmailLog.objects.create(
            user=cls.other,
            recipient_email=cls.other.email,
            email_type="welcome",
            subject="Other welcome",
            ses_message_id="ses-other",
        )
        SesEvent.objects.create(
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            message_id="delivery-studio-1283",
            raw_payload={},
            recipient_email=cls.primary.recipient_email,
            email_log=cls.primary,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_staff_boundary_sidebar_active_and_columns(self):
        response = self.client.get("/studio/email-log/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email log")
        self.assertContains(response, "Type / campaign")
        self.assertContains(response, "Welcome aboard")
        self.assertContains(response, "Not recorded")
        self.assertContains(response, 'aria-current="page"')
        self.client.force_login(self.member)
        denied = self.client.get("/studio/email-log/")
        self.assertNotEqual(denied.status_code, 200)

    def test_partial_case_insensitive_search_returns_only_snapshot_matches(self):
        response = self.client.get("/studio/email-log/?q=MEMBER@EXAMPLE")
        ids = [row["log"].pk for row in response.context["rows"]]
        self.assertEqual(ids, [self.primary.pk])

    def test_exact_alias_search_expands_canonical_history_once(self):
        response = self.client.get("/studio/email-log/?q=MEMBER-OLD@example.com")
        ids = [row["log"].pk for row in response.context["rows"]]
        self.assertCountEqual(ids, [self.primary.pk, self.alias.pk, self.owned_old.pk])

    def test_kind_is_dynamic_exact_and_unknown_is_filtered_empty(self):
        response = self.client.get("/studio/email-log/?kind=campaign")
        self.assertEqual([row["log"].pk for row in response.context["rows"]], [self.alias.pk])
        self.assertIn(("account_notice", "Account notice"), response.context["kind_choices"])
        empty = self.client.get("/studio/email-log/?kind=unknown-kind")
        self.assertContains(empty, "No emails match these filters.")
        self.assertContains(empty, "Clear filters")

    def test_disposition_and_links_are_safe(self):
        response = self.client.get("/studio/email-log/?status=delivered")
        self.assertEqual(response.context["rows"][0]["log"].pk, self.primary.pk)
        self.assertContains(response, "Delivered")
        row = response.context["rows"][0]
        parsed = urlparse(row["ses_events_url"])
        self.assertEqual(parse_qs(parsed.query)["q"], ["Member@Example.com"])
        self.assertEqual(row["recipient_user"], self.member)

    def test_external_recipient_is_plain_text(self):
        response = self.client.get("/studio/email-log/?q=external@example.net")
        self.assertContains(response, "external@example.net")
        self.assertNotContains(response, "email-log-recipient-link")

    def test_dates_are_inclusive_invalid_ignored_and_reversed_empty(self):
        today = timezone.now().date()
        EmailLog.objects.filter(pk=self.primary.pk).update(
            sent_at=timezone.make_aware(
                datetime.datetime.combine(today, datetime.time(23, 59)),
                datetime.UTC,
            )
        )
        included = self.client.get(
            f"/studio/email-log/?q=member%40example&since={today}&until={today}"
        )
        self.assertEqual(included.context["filtered_total"], 1)
        invalid = self.client.get("/studio/email-log/?since=garbage")
        self.assertEqual(invalid.context["filtered_total"], EmailLog.objects.count())
        reversed_range = self.client.get(
            "/studio/email-log/?since=2030-01-02&until=2030-01-01"
        )
        self.assertEqual(reversed_range.context["filtered_total"], 0)

    def test_pagination_clamps_and_preserves_every_filter(self):
        for index in range(52):
            EmailLog.objects.create(
                recipient_email=f"page-{index}@example.com",
                email_type="bulk",
                subject="Bulk",
            )
        response = self.client.get(
            "/studio/email-log/?kind=bulk&status=sent&since=2020-01-01&until=2030-01-01&q=page&page=999"
        )
        self.assertEqual(response.context["page"].number, 2)
        self.assertIn("kind=bulk", response.context["pager_prev_url"])
        self.assertIn("status=sent", response.context["pager_prev_url"])
        self.assertIn("since=2020-01-01", response.context["pager_prev_url"])
        self.assertIn("until=2030-01-01", response.context["pager_prev_url"])
        self.assertIn("q=page", response.context["pager_prev_url"])

    def test_list_query_count_does_not_grow_with_rows(self):
        with CaptureQueriesContext(connection) as small_capture:
            response = self.client.get("/studio/email-log/")
            self.assertEqual(response.status_code, 200)

        for index in range(25):
            user = User.objects.create_user(email=f"query-{index}@example.com")
            EmailAlias.objects.create(user=user, email=f"query-old-{index}@example.com")
            EmailLog.objects.create(
                user=user,
                recipient_email=user.email,
                email_type="query-proof",
                subject=f"Query proof {index}",
            )

        with CaptureQueriesContext(connection) as large_capture:
            response = self.client.get("/studio/email-log/")
            self.assertEqual(response.status_code, 200)

        self.assertLessEqual(len(large_capture), len(small_capture) + 1)


class UserEmailHistoryTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="staff-history@example.com", is_staff=True)
        self.user = User.objects.create_user(email="history@example.com")
        self.other = User.objects.create_user(email="else@example.com")
        EmailAlias.objects.create(user=self.user, email="alias-history@example.com")
        self.client.force_login(self.staff)

    def test_card_shows_canonical_newest_ten_and_links(self):
        for index in range(11):
            EmailLog.objects.create(
                user=self.user if index % 2 else None,
                recipient_email=(
                    f"owned-{index}@example.net" if index % 2
                    else "alias-history@example.com"
                ),
                email_type="notice",
                subject=f"Subject {index}",
            )
        EmailLog.objects.create(
            user=self.other, recipient_email=self.other.email,
            email_type="notice", subject="Leak",
        )

        response = self.client.get(f"/studio/users/{self.user.pk}/")

        self.assertEqual(len(response.context["email_history"]), 10)
        self.assertNotContains(response, "Leak")
        self.assertContains(response, "user-email-history-view-all")
        self.assertContains(response, "q=history%40example.com")
        self.assertContains(response, f"user={self.user.pk}")
        self.assertNotContains(response, "Email log API")

    def test_no_history_card_does_not_disappear(self):
        response = self.client.get(f"/studio/users/{self.user.pk}/")
        self.assertContains(response, "Email history")
        self.assertContains(response, "No outbound emails have been logged for this user.")

    def test_ses_user_filter_unions_fk_primary_alias_and_composes(self):
        events = [
            SesEvent.objects.create(message_id="fk", event_type="delivery", raw_payload={}, user=self.user, recipient_email="other@x.test"),
            SesEvent.objects.create(message_id="primary", event_type="delivery", raw_payload={}, recipient_email=self.user.email),
            SesEvent.objects.create(message_id="alias", event_type="delivery", raw_payload={}, recipient_email="ALIAS-HISTORY@example.com"),
            SesEvent.objects.create(message_id="wrong-type", event_type="open", raw_payload={}, user=self.user, recipient_email=self.user.email),
            SesEvent.objects.create(message_id="unrelated", event_type="delivery", raw_payload={}, recipient_email=self.other.email),
        ]
        response = self.client.get(
            f"/studio/ses-events/?user={self.user.pk}&type=delivery"
        )
        ids = [row["event"].pk for row in response.context["rows"]]
        self.assertCountEqual(ids, [event.pk for event in events[:3]])
        self.assertContains(response, f'name="user" value="{self.user.pk}"')
