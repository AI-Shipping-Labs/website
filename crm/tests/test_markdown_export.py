"""CRM Markdown archive coverage for issue #1304."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Token
from analytics.models import UserActivity
from api.openapi import build_spec
from api.urls import urlpatterns
from api.views.crm_export import build_single_crm_record_aggregate
from community.models import STATUS_CANCELED, BookedCall, CallHost
from crm.models import CRMRecord
from crm.services.markdown_export import (
    SECTION_TITLES,
    render_crm_record_markdown,
)
from plans.models import InterviewNote, Plan, Resource, Sprint

User = get_user_model()


class CRMMarkdownExportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@example.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@example.com",
            password="pw",
            first_name="Zoë",
            last_name="Builder",
        )
        cls.nonstaff = User.objects.create_user(
            email="reader@example.com", password="pw",
        )
        cls.record = CRMRecord.objects.create(
            user=cls.member,
            created_by=cls.staff,
            summary="A relationship summary",
            next_steps="Follow up",
        )
        cls.token = Token.objects.create(user=cls.staff, name="crm-export")

    def setUp(self):
        UserActivity.objects.filter(user=self.member).delete()
        self.client.login(email=self.staff.email, password="pw")

    @property
    def studio_url(self):
        return f"/studio/crm/{self.record.pk}/download.md"

    @property
    def api_url(self):
        return f"/api/crm/{self.member.email}/export.md"

    def auth(self, token=None):
        token = token or self.token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def test_studio_download_contract_and_stable_empty_sections(self):
        response = self.client.get(self.studio_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="crm-record-{self.record.pk}.md"',
        )
        document = response.content.decode()
        self.assertTrue(document.endswith("\n"))
        self.assertFalse(document.endswith("\n\n"))
        self.assertNotIn("\r", document)
        self.assertIn("# CRM record: Zoë Builder", document)
        self.assertIn("- Email verified: No", document)
        self.assertIn("- Soft bounce count: 0", document)
        for title in SECTION_TITLES:
            self.assertEqual(document.count(f"## {title}"), 1)
        self.assertIn("_No onboarding responses._", document)
        self.assertIn("_No recorded activity._", document)
        self.assertIn("_No sprint plans._", document)
        self.assertIn("_No sprint enrollments._", document)
        self.assertIn("_No course enrollments._", document)
        self.assertIn("_No booked calls._", document)
        self.assertIn("_No member notes._", document)

    def test_detail_shows_direct_download_after_profile_action(self):
        response = self.client.get(f"/studio/crm/{self.record.pk}/")
        self.assertContains(response, 'data-testid="crm-detail-download-markdown"')
        html = response.content.decode()
        self.assertLess(
            html.index('data-testid="crm-detail-open-profile"'),
            html.index('data-testid="crm-detail-download-markdown"'),
        )
        self.assertIn("min-h-[44px]", html)

    def test_studio_access_control_and_missing_record(self):
        self.client.logout()
        anonymous = self.client.get(self.studio_url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/accounts/login/?next=", anonymous.url)

        self.client.login(email=self.nonstaff.email, password="pw")
        forbidden = self.client.get(self.studio_url)
        self.assertEqual(forbidden.status_code, 403)
        self.assertNotContains(forbidden, self.member.email, status_code=403)
        self.assertNotContains(forbidden, "A relationship summary", status_code=403)

        self.client.login(email=self.staff.email, password="pw")
        self.assertEqual(self.client.get("/studio/crm/999999/download.md").status_code, 404)

    def test_api_parity_headers_auth_errors_and_method(self):
        fixed = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.UTC)
        with patch("api.views.crm_export.timezone.now", return_value=fixed):
            studio = self.client.get(self.studio_url)
            api = self.client.get(self.api_url, **self.auth())
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.content, studio.content)
        self.assertEqual(api["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(api["Content-Disposition"], studio["Content-Disposition"])

        self.assertEqual(self.client.get(self.api_url).status_code, 401)
        self.assertEqual(
            self.client.get(
                self.api_url,
                HTTP_AUTHORIZATION="Token invalid",
            ).status_code,
            401,
        )
        post = self.client.post(self.api_url, **self.auth())
        self.assertEqual(post.status_code, 405)

        unknown = self.client.get(
            "/api/crm/unknown@example.com/export.md", **self.auth(),
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["code"], "user_not_found")

        untracked = User.objects.create_user(email="untracked@example.com")
        missing = self.client.get(
            f"/api/crm/{untracked.email}/export.md", **self.auth(),
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "crm_record_not_found")

    def test_nonstaff_token_is_401_without_private_content(self):
        former_staff = User.objects.create_user(
            email="former@example.com", is_staff=True,
        )
        token = Token.objects.create(user=former_staff, name="former")
        key = token.key
        former_staff.is_staff = False
        former_staff.save(update_fields=["is_staff"])
        response = self.client.get(
            self.api_url,
            HTTP_AUTHORIZATION=f"Token {key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotContains(response, self.member.email, status_code=401)
        self.assertNotContains(response, "A relationship summary", status_code=401)

    def test_activity_is_uncapped_ordered_and_marks_first_payment(self):
        start = timezone.now() - datetime.timedelta(days=2)
        rows = []
        for index in range(105):
            rows.append(UserActivity(
                user=self.member,
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
                label=f"Activity {index}",
                occurred_at=start + datetime.timedelta(minutes=index),
            ))
        rows.extend([
            UserActivity(
                user=self.member,
                event_type=UserActivity.EVENT_PAYMENT,
                label="First payment",
                occurred_at=start - datetime.timedelta(minutes=2),
            ),
            UserActivity(
                user=self.member,
                event_type=UserActivity.EVENT_PAYMENT,
                label="Renewal",
                occurred_at=start + datetime.timedelta(days=1),
            ),
        ])
        UserActivity.objects.bulk_create(rows)
        document = self.client.get(self.studio_url).content.decode()
        self.assertIn("Activity 0", document)
        self.assertIn("Activity 104", document)
        self.assertLess(document.index("Activity 104"), document.index("Activity 0"))
        first_section = document[document.index("First payment"):]
        self.assertIn("Paid-upgrade marker: Yes", first_section)
        self.assertNotIn("Showing 100", document)

    def test_internal_notes_render_once_and_json_export_shape_is_unchanged(self):
        sprint = Sprint.objects.create(
            name="Sprint", slug="sprint", start_date=datetime.date(2026, 7, 1),
        )
        plan = Plan.objects.create(member=self.member, sprint=sprint, goal="Ship")
        note = InterviewNote.objects.create(
            member=self.member,
            plan=plan,
            visibility="internal",
            body="Private operator note",
            source_metadata={"z": 1, "a": "two"},
            created_by=self.staff,
        )
        document = self.client.get(self.studio_url).content.decode()
        self.assertEqual(document.count("Private operator note"), 1)
        self.assertIn(f"### Note {note.pk}", document)
        self.assertLess(document.index('"a": "two"'), document.index('"z": 1'))

        json_response = self.client.get(
            f"/api/crm/export?email={self.member.email}", **self.auth(),
        )
        self.assertEqual(json_response.status_code, 200)
        member = json_response.json()["members"][0]
        self.assertNotIn("activities", member)
        self.assertNotIn("booked_calls", member)

    def test_active_booked_call_urls_render_safely_and_canceled_is_excluded(self):
        host = CallHost.objects.create(
            name="Valeria", slug="crm-export-valeria",
        )
        active = BookedCall.objects.create(
            host=host,
            member=self.member,
            invitee_email=self.member.email,
            invitee_name="Member Builder",
            scheduled_at=timezone.now() + datetime.timedelta(days=2),
            calendly_event_uri="https://api.calendly.com/scheduled_events/ACTIVE",
            calendly_invitee_uri="https://api.calendly.com/invitees/ACTIVE",
            reschedule_url=(
                "https://calendly.com/resched/(active)?utm_source=crm"
            ),
            cancel_url="https://calendly.com/cancellations/active",
        )
        BookedCall.objects.create(
            host=host,
            member=self.member,
            invitee_email=self.member.email,
            status=STATUS_CANCELED,
            calendly_event_uri="https://api.calendly.com/scheduled_events/CANCELED",
            reschedule_url="https://calendly.com/resched/canceled-sentinel",
            cancel_url="https://calendly.com/cancellations/canceled-sentinel",
        )

        aggregate = build_single_crm_record_aggregate(
            self.record,
            bearer=self.staff,
        )
        self.assertEqual(len(aggregate["booked_calls"]), 1)
        self.assertEqual(aggregate["booked_calls"][0]["id"], active.pk)
        self.assertEqual(
            aggregate["booked_calls"][0]["reschedule_url"],
            "https://calendly.com/resched/(active)?utm_source=crm",
        )
        document = render_crm_record_markdown(aggregate)
        self.assertIn(
            "[Reschedule call](https://calendly.com/resched/%28active%29?utm_source=crm)",
            document,
        )
        self.assertIn(
            "[Cancel call](https://calendly.com/cancellations/active)",
            document,
        )
        self.assertNotIn("canceled-sentinel", document)

    def test_secret_bearing_or_unsafe_booked_call_urls_are_not_emitted(self):
        host = CallHost.objects.create(
            name="Alexey", slug="crm-export-alexey",
        )
        BookedCall.objects.create(
            host=host,
            member=self.member,
            invitee_email=self.member.email,
            calendly_event_uri="https://api.calendly.com/scheduled_events/SAFE",
            reschedule_url="https://calendly.com/resched/value?token=do-not-export",
            cancel_url="javascript:alert(1)",
        )
        document = self.client.get(self.studio_url).content.decode()
        self.assertIn("- Reschedule URL: _Not specified._", document)
        self.assertIn("- Cancellation URL: _Not specified._", document)
        self.assertNotIn("do-not-export", document)
        self.assertNotIn("javascript", document)

    def test_encoded_camelcase_and_ambiguous_query_keys_fail_closed(self):
        host = CallHost.objects.create(
            name="Privacy host", slug="crm-export-privacy-host",
        )
        unsafe_urls = [
            (
                "https://calendly.com/resched?%74oken=percent-token-secret",
                "percent-token-secret",
            ),
            (
                "https://calendly.com/resched?accessToken=camel-secret",
                "camel-secret",
            ),
            (
                "https://calendly.com/resched?api%5Fkey=encoded-key-secret",
                "encoded-key-secret",
            ),
            (
                "https://calendly.com/resched?bad%Q0key=malformed-secret",
                "malformed-secret",
            ),
            (
                "https://calendly.com/resched?safe%2520key=double-secret",
                "double-secret",
            ),
            (
                "https://calendly.com/resched?safe=one&safe=duplicate-secret",
                "duplicate-secret",
            ),
        ]
        for index, (unsafe_url, _secret) in enumerate(unsafe_urls):
            BookedCall.objects.create(
                host=host,
                member=self.member,
                invitee_email=self.member.email,
                calendly_event_uri=(
                    "https://api.calendly.com/scheduled_events/PRIVACY"
                    f"{index}"
                ),
                reschedule_url=unsafe_url,
            )

        document = self.client.get(self.studio_url).content.decode()
        for _unsafe_url, secret in unsafe_urls:
            self.assertNotIn(secret, document)
        self.assertEqual(
            document.count("- Reschedule URL: _Not specified._"),
            len(unsafe_urls),
        )

    def test_benign_query_key_substrings_remain_valid_on_plan_resources(self):
        sprint = Sprint.objects.create(
            name="Safe links sprint",
            slug="safe-links-sprint",
            start_date=datetime.date(2026, 7, 20),
        )
        plan = Plan.objects.create(member=self.member, sprint=sprint)
        safe_url = (
            "https://docs.example.com/guide"
            "?author=alexey&keyboard=compact&monkey=banana"
        )
        Resource.objects.create(
            plan=plan,
            title="Safe query resource",
            url=safe_url,
        )

        document = self.client.get(self.studio_url).content.decode()
        self.assertIn(
            "[Safe query resource]"
            "(https://docs.example.com/guide"
            "?author=alexey&keyboard=compact&monkey=banana)",
            document,
        )

    def test_calendly_api_uris_use_same_fail_closed_link_policy(self):
        host = CallHost.objects.create(
            name="URI privacy host",
            slug="crm-export-uri-privacy-host",
        )
        BookedCall.objects.create(
            host=host,
            member=self.member,
            invitee_email=self.member.email,
            calendly_event_uri=(
                "https://api.calendly.com/scheduled_events/SAFE-EVENT"
            ),
            calendly_invitee_uri=(
                "https://api.calendly.com/scheduled_events/SAFE-EVENT"
                "/invitees/SAFE-INVITEE"
            ),
        )
        BookedCall.objects.create(
            host=host,
            member=self.member,
            invitee_email=self.member.email,
            calendly_event_uri=(
                "https://api.calendly.com/scheduled_events/EVENT"
                "?token=literal-event-secret"
            ),
            calendly_invitee_uri=(
                "https://api.calendly.com/invitees/INVITEE"
                "?%74oken=encoded-invitee-secret"
            ),
        )
        BookedCall.objects.create(
            host=host,
            member=self.member,
            invitee_email=self.member.email,
            calendly_event_uri="javascript:literal-unsafe-event-secret",
            calendly_invitee_uri="data:text/plain,unsafe-invitee-secret",
        )

        document = self.client.get(self.studio_url).content.decode()
        self.assertIn(
            "[Open Calendly event]"
            "(https://api.calendly.com/scheduled_events/SAFE-EVENT)",
            document,
        )
        self.assertIn(
            "[Open Calendly invitee]"
            "(https://api.calendly.com/scheduled_events/SAFE-EVENT"
            "/invitees/SAFE-INVITEE)",
            document,
        )
        for secret in (
            "literal-event-secret",
            "encoded-invitee-secret",
            "literal-unsafe-event-secret",
            "unsafe-invitee-secret",
        ):
            self.assertNotIn(secret, document)
        self.assertEqual(
            document.count("- Calendly event URI: _Not specified._"),
            2,
        )
        self.assertEqual(
            document.count("- Calendly invitee URI: _Not specified._"),
            2,
        )

    def test_multi_plan_multi_note_queries_are_bounded(self):
        for index in range(3):
            sprint = Sprint.objects.create(
                name=f"Sprint {index}", slug=f"sprint-{index}",
                start_date=datetime.date(2026, 7, index + 1),
            )
            plan = Plan.objects.create(member=self.member, sprint=sprint)
            for note_index in range(3):
                InterviewNote.objects.create(
                    member=self.member,
                    plan=plan,
                    visibility="internal",
                    body=f"note {index}-{note_index}",
                )
        with CaptureQueriesContext(connection) as captured:
            aggregate = build_single_crm_record_aggregate(
                self.record,
                bearer=self.staff,
            )
        self.assertEqual(len(aggregate["plans"]), 3)
        self.assertEqual(len(aggregate["notes"]), 9)
        self.assertLessEqual(len(captured), 35)

    def test_openapi_documents_markdown_route(self):
        operation = build_spec(urlpatterns)["paths"]["/api/crm/{email}/export.md"]["get"]
        self.assertEqual(operation["tags"], ["CRM"])
        self.assertIn("401", operation["responses"])
        self.assertIn("404", operation["responses"])
        self.assertIn("405", operation["responses"])


class CRMMarkdownRendererSafetyTest(TestCase):
    def test_hostile_text_cannot_change_hierarchy_or_close_json_fence(self):
        aggregate = {
            "display_name": "# <script> `name`",
            "email": "evil@example.com",
            "tier": {},
            "base_tier": {},
            "tier_override_active": False,
            "email_verified": False,
            "unsubscribed": False,
            "soft_bounce_count": 0,
            "slack_member": False,
            "email_preferences": {"payload": "```\n<script>"},
            "import_metadata": {},
            "crm_record": {
                "status": "active",
                "persona": "- [x] injected\r\n## heading | [link](bad)",
                "summary": "Unicode: Žluťoučký",
                "next_steps": None,
                "created_at": None,
                "updated_at": None,
            },
            "onboarding_responses": [],
            "activities": [],
            "plans": [],
            "sprint_enrollments": [],
            "course_enrollments": [],
            "booked_calls": [],
            "notes": [{
                "id": 1,
                "body": "# note <b> [x] `tick`",
                "source_metadata": {"ticks": "````"},
            }],
            "export_metadata": {
                "studio_url": "https://aishippinglabs.com/studio/crm/1/",
                "crm_record_id": 1,
                "exported_at": "2026-07-20T12:00:00+00:00",
            },
        }
        document = render_crm_record_markdown(aggregate)
        self.assertEqual(sum(line.startswith("# ") for line in document.splitlines()), 1)
        for title in SECTION_TITLES:
            self.assertEqual(document.count(f"## {title}"), 1)
        self.assertNotIn("<script>", document)
        self.assertNotIn("<b>", document)
        self.assertIn(r"\# \<script\>", document)
        self.assertIn("Žluťoučký", document)
        self.assertNotIn("\r", document)
        self.assertIn("`````json", document)
