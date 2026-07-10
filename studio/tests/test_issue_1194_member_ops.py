"""Focused Studio coverage for issue #1194 member-ops surfaces."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailAlias
from community.models import CommunityAuditLog
from email_app.models import EmailCampaign, EmailLog, SesEvent
from events.models import Event, EventRegistration
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()
FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserDetailCrossLinksTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="issue1194-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="issue1194-member@test.com",
            password="pw",
            email_verified=True,
        )
        today = date.today()
        cls.current_sprint = Sprint.objects.create(
            name="Current Sprint",
            slug="issue1194-current",
            start_date=today - timedelta(days=7),
            duration_weeks=6,
        )
        cls.future_sprint = Sprint.objects.create(
            name="Needs Plan Sprint",
            slug="issue1194-needs-plan",
            start_date=today + timedelta(days=14),
            duration_weeks=6,
        )
        cls.plan = Plan.objects.create(
            member=cls.member,
            sprint=cls.current_sprint,
            goal="Ship direct links",
        )
        SprintEnrollment.objects.create(user=cls.member, sprint=cls.future_sprint)
        cls.event = Event.objects.create(
            title="Issue 1194 Event",
            slug="issue-1194-event",
            start_datetime=timezone.now() + timedelta(days=3),
            status="upcoming",
        )
        EventRegistration.objects.create(event=cls.event, user=cls.member)

    def setUp(self):
        self.client.login(email=self.staff.email, password="pw")

    def test_user_detail_context_includes_plans_sprints_and_events_without_crm(self):
        response = self.client.get(
            reverse("studio_user_detail", args=[self.member.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plans &amp; sprints")
        self.assertContains(response, "Current Sprint")
        self.assertContains(response, "Ship direct links")
        self.assertContains(response, "Needs Plan Sprint")
        self.assertContains(response, "No plan yet")
        self.assertContains(
            response,
            f"{reverse('studio_plan_create')}?user={self.member.pk}&amp;sprint={self.future_sprint.pk}",
        )
        self.assertContains(response, "Event registrations")
        self.assertContains(response, "Issue 1194 Event")
        self.assertContains(response, reverse("studio_event_edit", args=[self.event.pk]))

    def test_user_detail_empty_states_for_member_without_history(self):
        empty = User.objects.create_user(
            email="issue1194-empty@test.com", password="pw"
        )

        response = self.client.get(reverse("studio_user_detail", args=[empty.pk]))

        self.assertContains(response, "No plans or sprint enrollments yet.")
        self.assertContains(response, "No event registrations yet.")


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioDeliverabilityAndAliasActionsTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="issue1194-ops@test.com",
            password="pw",
            is_staff=True,
        )
        self.member = User.objects.create_user(
            email="issue1194-deliverable@test.com",
            password="pw",
            email_verified=True,
        )
        self.client.login(email=self.staff.email, password="pw")

    def test_permanent_bounce_action_unsubscribes_and_audits_once(self):
        response = self.client.post(
            reverse(
                "studio_user_deliverability_action",
                args=[self.member.pk, "permanent"],
            ),
            {"reason": "smtp 550"},
        )

        self.assertRedirects(
            response, reverse("studio_user_detail", args=[self.member.pk])
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.bounce_state, User.BounceState.PERMANENT)
        self.assertTrue(self.member.unsubscribed)
        self.assertEqual(self.member.last_bounce_diagnostic, "smtp 550")
        logs = CommunityAuditLog.objects.filter(
            user=self.member, action="api_mark_bounced"
        )
        self.assertEqual(logs.count(), 1)
        self.assertIn("source=studio", logs.get().details)
        self.assertIn("previous_state='none'", logs.get().details)
        self.assertIn("new_state='permanent'", logs.get().details)

    def test_clear_bounce_preserves_unsubscribed_and_audits_once(self):
        self.member.bounce_state = User.BounceState.PERMANENT
        self.member.unsubscribed = True
        self.member.last_bounce_diagnostic = "old"
        self.member.save(update_fields=[
            "bounce_state",
            "unsubscribed",
            "last_bounce_diagnostic",
        ])

        self.client.post(
            reverse(
                "studio_user_deliverability_action",
                args=[self.member.pk, "clear"],
            ),
            {"reason": "member fixed mailbox"},
        )

        self.member.refresh_from_db()
        self.assertEqual(self.member.bounce_state, User.BounceState.NONE)
        self.assertTrue(self.member.unsubscribed)
        self.assertEqual(self.member.last_bounce_diagnostic, "")
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=self.member, action="api_mark_bounced"
            ).count(),
            1,
        )

    def test_deliverability_post_requires_csrf_when_enforced(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.staff.email, password="pw")
        response = csrf_client.post(
            reverse(
                "studio_user_deliverability_action",
                args=[self.member.pk, "soft"],
            ),
            {"reason": "missing csrf"},
        )

        self.assertEqual(response.status_code, 403)

    def test_alias_add_remove_normalizes_and_audits(self):
        self.client.post(
            reverse("studio_user_alias_add", args=[self.member.pk]),
            {"alias_email": "Relay@Example.COM", "note": "relay"},
        )

        alias = EmailAlias.objects.get(user=self.member)
        self.assertEqual(alias.email, "relay@example.com")
        self.assertEqual(alias.note, "relay")
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=self.member, action="email_alias_added"
            ).count(),
            1,
        )

        self.client.post(
            reverse("studio_user_alias_remove", args=[self.member.pk]),
            {"alias_email": "relay@example.com"},
        )

        self.assertFalse(EmailAlias.objects.filter(user=self.member).exists())
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=self.member, action="email_alias_removed"
            ).count(),
            1,
        )

    def test_alias_primary_email_conflict_matches_api_rule(self):
        User.objects.create_user(email="taken@example.com", password="pw")

        response = self.client.post(
            reverse("studio_user_alias_add", args=[self.member.pk]),
            {"alias_email": "taken@example.com"},
            follow=True,
        )

        self.assertContains(
            response, "Alias email is already a primary account email."
        )
        self.assertFalse(EmailAlias.objects.filter(user=self.member).exists())


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioCampaignRecipientsAndSesFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="issue1194-campaign-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="issue1194-campaign-member@test.com",
            password="pw",
            email_verified=True,
        )
        cls.draft = EmailCampaign.objects.create(
            subject="Draft Preview",
            body="Hi",
            status="draft",
            target_min_level=0,
        )
        cls.sent = EmailCampaign.objects.create(
            subject="Sent Trace",
            body="Hi",
            status="sent",
            target_min_level=0,
        )
        cls.log = EmailLog.objects.create(
            campaign=cls.sent,
            user=cls.member,
            email_type="campaign",
            opens=2,
            clicks=1,
            bounced_at=timezone.now(),
            bounce_type="Permanent",
            bounce_subtype="General",
            bounce_diagnostic="smtp 550 missing",
            ses_message_id="issue1194-message",
        )
        cls.event = SesEvent.objects.create(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            message_id="issue1194-sns",
            raw_payload={"ok": True},
            recipient_email=cls.member.email,
            user=cls.member,
            email_log=cls.log,
            bounce_type="Permanent",
            diagnostic_code="smtp 550 missing",
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password="pw")

    def test_draft_campaign_recipient_preview_links_users(self):
        response = self.client.get(
            reverse("studio_campaign_recipients", args=[self.draft.pk])
        )

        self.assertContains(response, "Draft preview, not sent yet")
        self.assertContains(response, self.member.email)
        self.assertContains(
            response, reverse("studio_user_detail", args=[self.member.pk])
        )

    def test_sent_campaign_recipient_rows_show_bounce_diagnostic(self):
        response = self.client.get(
            reverse("studio_campaign_recipients", args=[self.sent.pk])
        )

        self.assertContains(response, "Actual send log recipients")
        self.assertContains(response, "Bounced")
        self.assertContains(response, "smtp 550 missing")

    def test_ses_campaign_filter_composes_with_type_and_search(self):
        other_campaign = EmailCampaign.objects.create(
            subject="Other",
            body="Hi",
            status="sent",
        )
        other_log = EmailLog.objects.create(
            campaign=other_campaign,
            user=self.member,
            email_type="campaign",
            ses_message_id="issue1194-other-message",
        )
        SesEvent.objects.create(
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            message_id="issue1194-other-sns",
            raw_payload={"ok": True},
            recipient_email=self.member.email,
            user=self.member,
            email_log=other_log,
        )

        response = self.client.get(
            reverse("studio_ses_event_list"),
            {
                "campaign": self.sent.pk,
                "type": SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
                "q": "campaign-member",
            },
        )

        rows = response.context["rows"]
        self.assertEqual([row["event"].pk for row in rows], [self.event.pk])
        self.assertContains(response, "Sent Trace")
