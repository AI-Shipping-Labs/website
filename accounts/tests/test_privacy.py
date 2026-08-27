import json
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.sessions.models import Session
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import (
    SIGNUP_SOURCE_NEWSLETTER,
    EmailAlias,
    MemberAPIKey,
    PrivacyRequestLog,
    User,
)
from accounts.services.privacy import (
    REDACTED,
    SCHEMA_VERSION,
    _book_club_export,
    _comments_export,
    build_user_data_export,
    delete_account_for_privacy,
)
from analytics.models import UserActivity
from bookclub.models import Book, Chapter, ChapterRead, Note, ReaderProfile
from comments.models import Comment
from community.models import UnmatchedBookedCall
from content.models import (
    Course,
    Enrollment,
    Module,
    Project,
    Unit,
    UserContentCompletion,
    Workshop,
    WorkshopPage,
)
from content.models.download import Download, DownloadDeliveryGrant
from content.models.peer_review import CourseCertificate
from crm.models import CRMRecord, SlackMessage, SlackThread
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from integrations.models import WebhookLog
from notifications.models import Notification
from payments.models import (
    ConversionAttribution,
    PaymentAccountMismatch,
    WebhookEvent,
)
from plans.models import Plan, Sprint
from tests.fixtures import TierSetupMixin
from voting.models import Poll, PollOption, PollVote


def _course(slug="privacy-course"):
    return Course.objects.create(
        slug=slug,
        title="Privacy Course",
        description="Course",
        status="published",
    )


def _event(slug="privacy-event"):
    return Event.objects.create(
        slug=slug,
        title="Privacy Event",
        description="Event",
        start_datetime=timezone.now() + timedelta(days=3),
        status="upcoming",
    )


def _sprint(slug="privacy-sprint"):
    return Sprint.objects.create(
        slug=slug,
        name="Privacy Sprint",
        start_date=date(2026, 7, 1),
        duration_weeks=4,
        status="active",
        min_tier_level=0,
    )


@tag("core")
class PrivacyAccountViewTest(TestCase):
    def test_privacy_section_renders_for_member_and_newsletter_only_user(self):
        member = User.objects.create_user(email="member-privacy@test.com")
        self.client.force_login(member)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="privacy-data-section"')
        self.assertContains(response, 'data-testid="privacy-export-link"')
        self.assertContains(response, 'data-testid="privacy-request-form"')

        newsletter = User.objects.create_user(
            email="newsletter-privacy@test.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )
        self.client.force_login(newsletter)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="newsletter-only-cta"')
        self.assertContains(response, 'data-testid="privacy-data-section"')
        self.assertContains(response, 'data-testid="privacy-export-link"')

    def test_anonymous_export_and_deletion_request_use_login_redirect(self):
        export_response = self.client.get("/account/api/data-export")
        request_response = self.client.post("/account/api/request-deletion")

        self.assertEqual(export_response.status_code, 302)
        self.assertIn("/accounts/login/", export_response.url)
        self.assertEqual(request_response.status_code, 302)
        self.assertIn("/accounts/login/", request_response.url)


@tag("core")
class PrivacyExportTest(TierSetupMixin, TestCase):
    def test_data_export_returns_attachment_json_and_audits(self):
        user = User.objects.create_user(
            email="export@test.com",
            password="TestPass123!",
            first_name="Export",
            email_verified=True,
        )
        user.email_preferences = {"newsletter": True}
        user.dashboard_dismissals = ["slack_join"]
        user.tier = self.main_tier
        user.stripe_customer_id = "cus_export"
        user.slack_user_id = "U_EXPORT"
        user.save(
            update_fields=[
                "email_preferences",
                "dashboard_dismissals",
                "tier",
                "stripe_customer_id",
                "slack_user_id",
            ]
        )
        EmailAlias.objects.create(user=user, email="alias-export@test.com")
        member_key, plaintext = MemberAPIKey.create_for_user(
            user=user,
            name="local codex",
        )
        course = _course()
        Enrollment.objects.create(user=user, course=course)
        UserContentCompletion.objects.create(
            user=user,
            content_type="workshop_page",
            object_id=123,
            completed_at=timezone.now(),
        )
        event = _event()
        EventRegistration.objects.create(event=event, user=user)
        sprint = _sprint()
        plan = Plan.objects.create(member=user, sprint=sprint, goal="Ship GDPR")
        SlackThread.objects.create(
            channel_id="C123",
            thread_ts="111.222",
            member=user,
            plan=plan,
            posted_at=timezone.now(),
        )
        other_thread = SlackThread.objects.create(
            channel_id="C123",
            thread_ts="111.333",
            slack_user_id="U_OTHER",
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=other_thread,
            ts="111.334",
            slack_user_id="U_EXPORT",
            text="reply authored in another thread",
            posted_at=timezone.now(),
        )
        EmailLog.objects.create(user=user, email_type="welcome")
        UserActivity.objects.create(
            user=user,
            event_type=UserActivity.EVENT_EVENT_REGISTER,
            occurred_at=timezone.now(),
            label="Registered",
        )

        self.client.force_login(user)
        response = self.client.get("/account/api/data-export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn(
            'attachment; filename="ai-shipping-labs-data-',
            response["Content-Disposition"],
        )
        payload = json.loads(response.content)
        self.assertEqual(payload["manifest"]["primary_email"], "export@test.com")
        self.assertEqual(
            payload["events_community"]["slack_authored_messages"][0]["text"],
            "reply authored in another thread",
        )
        self.assertEqual(payload["membership_payment"]["current_tier"]["slug"], "main")
        self.assertEqual(
            payload["auth_security"]["member_api_keys"][0]["lookup_prefix"],
            member_key.lookup_prefix,
        )
        self.assertEqual(
            payload["learning_content"]["course_enrollments"][0]["course_id"],
            course.pk,
        )
        self.assertEqual(
            payload["events_community"]["event_registrations"][0]["event_id"],
            event.pk,
        )
        self.assertEqual(payload["sprints_plans"]["plans"][0]["goal"], "Ship GDPR")
        self.assertEqual(
            payload["communications_activity"]["email_logs"][0]["email_type"],
            "welcome",
        )

        body = response.content.decode()
        self.assertNotIn(plaintext, body)
        self.assertNotIn(member_key.key_hash, body)
        self.assertNotIn("password", payload["auth_security"]["member_api_keys"][0])
        self.assertNotIn("card_number", body)

        log = PrivacyRequestLog.objects.get(request_type="export")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_COMPLETED)
        self.assertEqual(log.old_user_id, user.pk)
        self.assertEqual(log.email_domain, "test.com")
        self.assertNotIn("export@test.com", json.dumps(log.row_count_summary))

    def test_newsletter_only_export_has_empty_member_categories(self):
        user = User.objects.create_user(
            email="newsletter-export@test.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )
        payload = build_user_data_export(user)

        self.assertEqual(
            payload["manifest"]["primary_email"],
            "newsletter-export@test.com",
        )
        self.assertEqual(payload["learning_content"]["course_enrollments"], [])
        self.assertEqual(payload["events_community"]["event_registrations"], [])
        self.assertEqual(payload["sprints_plans"]["plans"], [])

    def test_oauth_social_account_export_redacts_provider_secrets(self):
        user = User.objects.create_user(email="oauth-export@test.com")
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google-stable-uid",
            extra_data={
                "email": "oauth-export@test.com",
                "name": "OAuth Export",
                "locale": "en",
                "picture": "https://example.com/avatar.png?sz=96",
                "access_token": "ya29.raw-access-token",
                "refresh_token": "raw-refresh-token",
                "nested": {
                    "client_secret": "raw-client-secret",
                    "authorization": "Bearer raw-authorization-token",
                    "public_profile": "builder",
                },
                "token_list": [
                    "gho_raw-github-token",
                    {
                        "id_token": ("aaaaaaaaaabbbbbbbbbb.ccccccccccdddddddddd.eeeeeeeeeeffffffffff"),
                    },
                ],
                "provider_values": [
                    "ghp_raw-provider-token",
                    {
                        "jwt_claim": ("1111111111aaaaaaaaaa.2222222222bbbbbbbbbb.3333333333cccccccccc"),
                    },
                ],
                "avatar_url": ("https://example.com/photo.png?size=96&access_token=raw-query-token"),
            },
        )

        payload = build_user_data_export(user)

        account = payload["auth_security"]["oauth_social_accounts"][0]
        metadata = account["extra_data"]
        self.assertEqual(account["provider"], "google")
        self.assertEqual(account["uid"], "google-stable-uid")
        self.assertEqual(metadata["email"], "oauth-export@test.com")
        self.assertEqual(metadata["name"], "OAuth Export")
        self.assertEqual(metadata["locale"], "en")
        self.assertEqual(metadata["picture"], "https://example.com/avatar.png?sz=96")
        self.assertEqual(metadata["nested"]["public_profile"], "builder")

        self.assertEqual(metadata["access_token"], REDACTED)
        self.assertEqual(metadata["refresh_token"], REDACTED)
        self.assertEqual(metadata["nested"]["client_secret"], REDACTED)
        self.assertEqual(metadata["nested"]["authorization"], REDACTED)
        self.assertEqual(metadata["token_list"], REDACTED)
        self.assertEqual(metadata["provider_values"][0], REDACTED)
        self.assertEqual(metadata["provider_values"][1]["jwt_claim"], REDACTED)

        body = json.dumps(payload)
        self.assertNotIn("raw-access-token", body)
        self.assertNotIn("raw-refresh-token", body)
        self.assertNotIn("raw-client-secret", body)
        self.assertNotIn("raw-authorization-token", body)
        self.assertNotIn("raw-github-token", body)
        self.assertNotIn("raw-provider-token", body)
        self.assertNotIn("raw-query-token", body)


@tag("core")
class PrivacyExportUuidSerialisationTest(TestCase):
    """Every UUID-valued export field must reach the member as a string."""

    def test_engaged_member_export_serialises_every_uuid_field_as_a_string(self):
        user = User.objects.create_user(email="uuid-export@test.com")
        course = _course("uuid-course")
        plan_content_id = Comment.objects.create(
            user=user,
            content_id="11111111-1111-4111-8111-111111111111",
            body="A comment on a thread",
        ).content_id
        poll = Poll.objects.create(title="Next topic", allow_proposals=True)
        proposed = PollOption.objects.create(
            poll=poll,
            title="Inference engineering",
            proposed_by=user,
        )
        vote = PollVote.objects.create(poll=poll, option=proposed, user=user)
        download = Download.objects.create(
            title="Cheatsheet",
            slug="uuid-cheatsheet",
            file_url="https://example.com/cheatsheet.pdf",
        )
        grant = DownloadDeliveryGrant.objects.create(
            user=user,
            download=download,
            token_hash="uuid-export-token-hash",
            expires_at=timezone.now() + timedelta(days=1),
        )
        certificate = CourseCertificate.objects.create(user=user, course=course)

        self.client.force_login(user)
        response = self.client.get("/account/api/data-export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = json.loads(response.content)

        comment_row = payload["communications_activity"]["comments"][0]
        vote_row = payload["communications_activity"]["poll_votes"][0]
        proposal_row = payload["communications_activity"]["poll_proposals"][0]
        grant_row = payload["learning_content"]["download_delivery_grants"][0]
        certificate_row = payload["learning_content"]["course_certificates"][0]

        self.assertEqual(comment_row["content_id"], str(plan_content_id))
        self.assertEqual(vote_row["poll_id"], str(poll.pk))
        self.assertEqual(vote_row["option_id"], str(proposed.pk))
        self.assertEqual(vote_row["id"], vote.pk)
        self.assertEqual(proposal_row["id"], str(proposed.pk))
        self.assertEqual(proposal_row["poll_id"], str(poll.pk))
        self.assertEqual(grant_row["id"], str(grant.pk))
        self.assertEqual(certificate_row["id"], str(certificate.pk))
        for value in (
            comment_row["content_id"],
            vote_row["poll_id"],
            vote_row["option_id"],
            proposal_row["id"],
            proposal_row["poll_id"],
            grant_row["id"],
            certificate_row["id"],
        ):
            self.assertIsInstance(value, str)

    @patch("accounts.views.account.build_user_data_export")
    def test_export_view_still_fails_loudly_on_a_non_plain_value(self, build_export):
        """No ``default=`` fallback: an unconverted object must not be stringified."""
        user = User.objects.create_user(email="loud-export@test.com")
        build_export.return_value = {"manifest": {}, "account_profile": user}

        self.client.force_login(user)

        with self.assertRaises(TypeError):
            self.client.get("/account/api/data-export")


@tag("core")
class PrivacyCommentContextExportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.exporting_user = User.objects.create_user(
            email="comment-export@test.com",
            first_name="Exporting",
        )
        cls.other_user = User.objects.create_user(
            email="private-owner@test.com",
            first_name="Private Owner",
        )

        cls.course = _course("comment-context-course")
        cls.course.title = "Search Systems"
        cls.course.save(update_fields=["title"])
        cls.module = Module.objects.create(
            course=cls.course,
            title="Indexes",
            slug="indexes",
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title="Vector search",
            slug="vector-search",
            content_id=uuid.uuid4(),
        )

        cls.workshop = Workshop.objects.create(
            slug="production-agents-comment-context",
            title="Production Agents",
            status="published",
            date=date(2026, 8, 27),
        )
        cls.workshop_page = WorkshopPage.objects.create(
            workshop=cls.workshop,
            slug="set-up-agent",
            title="Set up the agent",
            content_id=uuid.uuid4(),
        )

        cls.book = Book.objects.create(
            title="Inference Engineering",
            slug="inference-engineering-comment-context",
            author="Operator Author",
            status="current",
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book,
            number=3,
            title="Batching",
        )
        cls.other_note = Note.objects.create(
            chapter=cls.chapter,
            user=cls.other_user,
            body="private note source that must not leak",
        )
        cls.own_chapter = Chapter.objects.create(
            book=cls.book,
            number=4,
            title="Serving",
        )
        cls.own_note = Note.objects.create(
            chapter=cls.own_chapter,
            user=cls.exporting_user,
            body="the exporting member's own note",
        )

        cls.sprint = Sprint.objects.create(
            slug="august-shipping-comment-context",
            name="August Shipping Sprint",
            start_date=date(2026, 8, 1),
            duration_weeks=4,
            status="active",
            min_tier_level=0,
        )
        cls.other_plan = Plan.objects.create(
            member=cls.other_user,
            sprint=cls.sprint,
            title="Ship evaluation harness",
            goal="private plan goal that must not leak",
            summary_goal="private plan body that must not leak",
        )

    def test_comments_include_all_context_types_for_own_other_and_replies(self):
        course_comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.unit.content_id,
            body="How is this index built?",
        )
        workshop_comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.workshop_page.content_id,
            body="Where should this agent run?",
        )
        note_comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.other_note.comment_content_id,
            body="This batching observation helped",
        )
        note_reply = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.other_note.comment_content_id,
            parent=note_comment,
            body="Following up on the same thread",
        )
        own_note_comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.own_note.comment_content_id,
            body="A reminder on my own note",
        )
        plan_comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.other_plan.comment_content_id,
            body="How will you evaluate it?",
        )

        comments = _comments_export(self.exporting_user)
        rows = {row["body"]: row for row in comments}

        self.assertEqual(
            rows[course_comment.body]["content_type"],
            "course_unit",
        )
        self.assertEqual(
            rows[course_comment.body]["content_label"],
            "Course unit: Search Systems — Vector search",
        )
        self.assertEqual(
            rows[workshop_comment.body]["content_type"],
            "workshop_page",
        )
        self.assertEqual(
            rows[workshop_comment.body]["content_label"],
            "Workshop tutorial: Production Agents — Set up the agent",
        )
        expected_note_context = {
            "content_type": "book_club_note",
            "content_label": (
                "Book Club note: Inference Engineering — Chapter 3: Batching"
            ),
        }
        for comment in (note_comment, note_reply):
            self.assertEqual(
                {
                    "content_type": rows[comment.body]["content_type"],
                    "content_label": rows[comment.body]["content_label"],
                },
                expected_note_context,
            )
        self.assertEqual(rows[note_reply.body]["parent_id"], note_comment.pk)
        self.assertEqual(
            rows[own_note_comment.body]["content_label"],
            "Book Club note: Inference Engineering — Chapter 4: Serving",
        )
        self.assertEqual(
            rows[plan_comment.body]["content_type"],
            "sprint_plan",
        )
        self.assertEqual(
            rows[plan_comment.body]["content_label"],
            "Sprint plan: August Shipping Sprint — Ship evaluation harness",
        )

        course_row = rows[course_comment.body]
        self.assertEqual(
            list(course_row),
            [
                "id",
                "content_id",
                "parent_id",
                "body",
                "created_at",
                "updated_at",
                "content_type",
                "content_label",
            ],
        )
        self.assertEqual(course_row["id"], course_comment.pk)
        self.assertEqual(course_row["content_id"], str(self.unit.content_id))
        self.assertIsNone(course_row["parent_id"])
        self.assertEqual(course_row["created_at"], course_comment.created_at.isoformat())
        self.assertEqual(course_row["updated_at"], course_comment.updated_at.isoformat())

        rendered = json.dumps(comments)
        for private_value in (
            self.other_note.body,
            self.other_plan.goal,
            self.other_plan.summary_goal,
            self.other_user.email,
            self.other_user.first_name,
        ):
            self.assertNotIn(private_value, rendered)

    def test_unknown_owner_is_preserved(self):
        content_id = uuid.uuid4()
        comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=content_id,
            body="Keep this orphaned discussion",
        )

        row = _comments_export(self.exporting_user)[0]

        self.assertEqual(row["id"], comment.pk)
        self.assertEqual(row["content_id"], str(content_id))
        self.assertEqual(row["body"], comment.body)
        self.assertEqual(row["content_type"], "unknown")
        self.assertIsNone(row["content_label"])

    def test_collision_precedence_matches_notification_resolution(self):
        unit_id = self.unit.content_id
        workshop_id = self.workshop_page.content_id
        note_id = self.other_note.comment_content_id

        WorkshopPage.objects.create(
            workshop=self.workshop,
            slug="unit-collision",
            title="Unit collision page",
            content_id=unit_id,
        )
        unit_collision_chapter = Chapter.objects.create(
            book=self.book,
            number=10,
            title="Unit collision chapter",
        )
        Note.objects.create(
            chapter=unit_collision_chapter,
            user=self.other_user,
            body="unit collision note",
            comment_content_id=unit_id,
        )
        workshop_collision_chapter = Chapter.objects.create(
            book=self.book,
            number=11,
            title="Workshop collision chapter",
        )
        Note.objects.create(
            chapter=workshop_collision_chapter,
            user=self.other_user,
            body="workshop collision note",
            comment_content_id=workshop_id,
        )
        for index, content_id in enumerate((unit_id, workshop_id, note_id), start=1):
            sprint = Sprint.objects.create(
                slug=f"collision-sprint-{index}",
                name=f"Collision Sprint {index}",
                start_date=date(2026, 9, index),
                duration_weeks=1,
                status="draft",
                min_tier_level=0,
            )
            Plan.objects.create(
                member=self.other_user,
                sprint=sprint,
                title=f"Collision plan {index}",
                comment_content_id=content_id,
            )

        for body, content_id in (
            ("unit wins", unit_id),
            ("workshop wins", workshop_id),
            ("note wins", note_id),
            ("plan resolves", self.other_plan.comment_content_id),
        ):
            Comment.objects.create(
                user=self.exporting_user,
                content_id=content_id,
                body=body,
            )

        rows = {row["body"]: row for row in _comments_export(self.exporting_user)}

        self.assertEqual(rows["unit wins"]["content_type"], "course_unit")
        self.assertEqual(rows["workshop wins"]["content_type"], "workshop_page")
        self.assertEqual(rows["note wins"]["content_type"], "book_club_note")
        self.assertEqual(rows["plan resolves"]["content_type"], "sprint_plan")

    def test_optional_owner_models_degrade_to_unknown(self):
        comment = Comment.objects.create(
            user=self.exporting_user,
            content_id=self.unit.content_id,
            body="Known only when owner models are installed",
        )

        def optional_models_unavailable(app_label, model_name):
            if (app_label, model_name) == ("comments", "Comment"):
                return Comment
            return None

        with patch(
            "accounts.services.privacy._model",
            side_effect=optional_models_unavailable,
        ):
            rows = _comments_export(self.exporting_user)

        self.assertEqual(rows[0]["id"], comment.pk)
        self.assertEqual(rows[0]["content_type"], "unknown")
        self.assertIsNone(rows[0]["content_label"])

    def test_comment_context_query_count_is_fixed_at_five(self):
        light = User.objects.create_user(email="comment-query-light@test.com")
        heavy = User.objects.create_user(email="comment-query-heavy@test.com")
        Comment.objects.create(
            user=light,
            content_id=self.unit.content_id,
            body="one comment",
        )
        content_ids = (
            self.unit.content_id,
            self.workshop_page.content_id,
            self.other_note.comment_content_id,
            self.other_plan.comment_content_id,
        )
        for index in range(40):
            Comment.objects.create(
                user=heavy,
                content_id=content_ids[index % len(content_ids)],
                body=f"comment {index}",
            )

        with self.assertNumQueries(5):
            light_rows = _comments_export(light)
        with self.assertNumQueries(5):
            heavy_rows = _comments_export(heavy)

        self.assertEqual(len(light_rows), 1)
        self.assertEqual(len(heavy_rows), 40)


@tag("core")
class PrivacyBookClubExportTest(TestCase):
    """``events_community.book_club`` carries the member's own reading only."""

    @classmethod
    def setUpTestData(cls):
        cls.book = Book.objects.create(
            title="Inference Engineering",
            slug="inference-engineering",
            author="Philip Kiely",
            status="current",
        )
        cls.chapter_one = Chapter.objects.create(
            book=cls.book, number=1, title="Batching",
        )
        cls.chapter_two = Chapter.objects.create(
            book=cls.book, number=2, title="Caching",
        )

    def test_reader_export_carries_reads_notes_and_stored_visibility(self):
        user = User.objects.create_user(email="bookclub-export@test.com")
        ChapterRead.objects.create(chapter=self.chapter_one, user=user)
        ChapterRead.objects.create(chapter=self.chapter_two, user=user)
        note = Note.objects.create(
            chapter=self.chapter_one,
            user=user,
            body="continuous batching is the win",
        )
        profile = ReaderProfile.objects.create(user=user, visibility="private")

        book_club = build_user_data_export(user)["events_community"]["book_club"]

        reads = book_club["chapter_reads"]
        self.assertEqual(len(reads), 2)
        self.assertEqual(
            [row["chapter_number"] for row in reads],
            [1, 2],
        )
        self.assertEqual(
            [row["chapter_title"] for row in reads],
            ["Batching", "Caching"],
        )
        self.assertEqual(reads[0]["book_slug"], "inference-engineering")
        self.assertEqual(reads[0]["book_title"], "Inference Engineering")
        self.assertEqual(reads[0]["chapter_id"], self.chapter_one.pk)
        self.assertEqual(
            reads[0]["read_at"],
            ChapterRead.objects.get(chapter=self.chapter_one, user=user).read_at.isoformat(),
        )

        self.assertEqual(len(book_club["notes"]), 1)
        note_row = book_club["notes"][0]
        self.assertEqual(note_row["body"], "continuous batching is the win")
        self.assertEqual(note_row["comment_content_id"], str(note.comment_content_id))
        self.assertIsInstance(note_row["comment_content_id"], str)
        self.assertEqual(note_row["chapter_title"], "Batching")
        self.assertEqual(note_row["book_slug"], "inference-engineering")
        self.assertEqual(note_row["created_at"], note.created_at.isoformat())
        self.assertEqual(note_row["updated_at"], note.updated_at.isoformat())
        self.assertNotIn("body_html", note_row)

        self.assertEqual(
            book_club["reader_profile"],
            {
                "visibility": "private",
                "has_explicit_setting": True,
                "created_at": profile.created_at.isoformat(),
                "updated_at": profile.updated_at.isoformat(),
            },
        )

    def test_reader_without_a_profile_row_reports_the_live_model_default(self):
        user = User.objects.create_user(email="bookclub-default@test.com")
        ChapterRead.objects.create(chapter=self.chapter_one, user=user)

        profile_export = build_user_data_export(user)["events_community"]["book_club"]["reader_profile"]

        field_default = ReaderProfile._meta.get_field("visibility").get_default()
        self.assertEqual(profile_export["visibility"], field_default)
        self.assertFalse(profile_export["has_explicit_setting"])
        self.assertIsNone(profile_export["created_at"])
        self.assertIsNone(profile_export["updated_at"])

    def test_flipping_the_model_default_flips_the_export_without_touching_privacy(self):
        user = User.objects.create_user(email="bookclub-flip@test.com")
        visibility_field = ReaderProfile._meta.get_field("visibility")
        original_default = visibility_field.default
        flipped = "public" if original_default == "private" else "private"
        visibility_field.default = flipped
        try:
            profile_export = build_user_data_export(user)["events_community"]["book_club"]["reader_profile"]
        finally:
            visibility_field.default = original_default

        self.assertEqual(profile_export["visibility"], flipped)
        self.assertNotEqual(profile_export["visibility"], original_default)

    def test_export_never_leaks_another_readers_notes_reads_or_profile(self):
        mine = User.objects.create_user(email="bookclub-mine@test.com")
        theirs = User.objects.create_user(email="bookclub-theirs@test.com")
        ChapterRead.objects.create(chapter=self.chapter_one, user=mine)
        ChapterRead.objects.create(chapter=self.chapter_one, user=theirs)
        Note.objects.create(chapter=self.chapter_one, user=mine, body="my own note")
        other_note = Note.objects.create(
            chapter=self.chapter_one, user=theirs, body="their private note",
        )
        ReaderProfile.objects.create(user=theirs, visibility="public")

        payload = build_user_data_export(mine)
        book_club = payload["events_community"]["book_club"]

        self.assertEqual([row["body"] for row in book_club["notes"]], ["my own note"])
        self.assertEqual(len(book_club["chapter_reads"]), 1)
        self.assertFalse(book_club["reader_profile"]["has_explicit_setting"])
        rendered = json.dumps(payload)
        self.assertNotIn("their private note", rendered)
        self.assertNotIn(str(other_note.comment_content_id), rendered)
        self.assertNotIn("bookclub-theirs@test.com", rendered)

    def test_member_without_book_club_activity_gets_populated_empty_structures(self):
        user = User.objects.create_user(
            email="bookclub-none@test.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )

        payload = build_user_data_export(user)
        book_club = payload["events_community"]["book_club"]

        self.assertEqual(book_club["chapter_reads"], [])
        self.assertEqual(book_club["notes"], [])
        self.assertEqual(
            book_club["reader_profile"]["visibility"],
            ReaderProfile._meta.get_field("visibility").get_default(),
        )
        self.assertFalse(book_club["reader_profile"]["has_explicit_setting"])
        json.dumps(payload)

    def test_book_club_export_query_count_does_not_grow_with_reading_volume(self):
        light = User.objects.create_user(email="bookclub-light@test.com")
        ChapterRead.objects.create(chapter=self.chapter_one, user=light)
        Note.objects.create(chapter=self.chapter_one, user=light, body="one note")
        ReaderProfile.objects.create(user=light, visibility="public")

        heavy = User.objects.create_user(email="bookclub-heavy@test.com")
        ReaderProfile.objects.create(user=heavy, visibility="public")
        chapters = []
        for book_index in range(3):
            book = Book.objects.create(
                title=f"Book {book_index}",
                slug=f"heavy-book-{book_index}",
                author="Author",
                status="finished",
            )
            for chapter_number in range(7):
                chapters.append(
                    Chapter.objects.create(
                        book=book,
                        number=chapter_number,
                        title=f"Chapter {chapter_number}",
                    )
                )
        for chapter in chapters[:20]:
            ChapterRead.objects.create(chapter=chapter, user=heavy)
        for chapter in chapters[:10]:
            Note.objects.create(chapter=chapter, user=heavy, body="heavy note")

        with self.assertNumQueries(3):
            light_export = _book_club_export(light)
        with self.assertNumQueries(3):
            heavy_export = _book_club_export(heavy)

        self.assertEqual(len(light_export["chapter_reads"]), 1)
        self.assertEqual(len(heavy_export["chapter_reads"]), 20)
        self.assertEqual(len(heavy_export["notes"]), 10)
        self.assertEqual(
            {row["book_slug"] for row in heavy_export["chapter_reads"]},
            {"heavy-book-0", "heavy-book-1", "heavy-book-2"},
        )

    def test_export_keeps_eight_top_level_sections_and_the_audit_count(self):
        user = User.objects.create_user(email="bookclub-sections@test.com")
        ChapterRead.objects.create(chapter=self.chapter_one, user=user)

        self.client.force_login(user)
        response = self.client.get("/account/api/data-export")

        payload = json.loads(response.content)
        self.assertEqual(payload["manifest"]["schema_version"], SCHEMA_VERSION)
        self.assertEqual(len(set(payload) - {"manifest"}), 8)
        self.assertIn("book_club", payload["events_community"])
        log = PrivacyRequestLog.objects.get(request_type="export")
        self.assertEqual(log.row_count_summary, {"exported_sections": 8})

    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_deleting_the_account_still_erases_book_club_rows(self, _notify):
        user = User.objects.create_user(email="bookclub-delete@test.com")
        ChapterRead.objects.create(chapter=self.chapter_one, user=user)
        Note.objects.create(chapter=self.chapter_one, user=user, body="erase me")
        ReaderProfile.objects.create(user=user, visibility="public")

        result = delete_account_for_privacy(user, {"ip": "127.0.0.1"})

        self.assertTrue(result.success)
        self.assertFalse(ChapterRead.objects.exists())
        self.assertFalse(Note.objects.exists())
        self.assertFalse(ReaderProfile.objects.exists())
        erased = result.row_count_summary["erased"]
        self.assertEqual(erased["bookclub.ChapterRead"], 1)
        self.assertEqual(erased["bookclub.Note"], 1)
        self.assertEqual(erased["bookclub.ReaderProfile"], 1)


@tag("core")
class PrivacyDeletionGuardTest(TierSetupMixin, TestCase):
    def test_staff_and_superuser_accounts_are_blocked_by_controlled_service(self):
        cases = [
            ("staff", {"is_staff": True}),
            ("superuser", {"is_superuser": True}),
        ]

        for label, flags in cases:
            with self.subTest(account_type=label):
                user = User.objects.create_user(
                    email=f"{label}-delete@test.com",
                    password="TestPass123!",
                    **flags,
                )
                old_user_id = user.pk

                result = delete_account_for_privacy(
                    user,
                    {
                        "ip": "192.0.2.20",
                        "user_agent": "controlled-operator-guard-test",
                    },
                )

                self.assertFalse(result.success)
                self.assertEqual(result.status, PrivacyRequestLog.STATUS_BLOCKED)
                self.assertEqual(
                    result.blocker_reason,
                    PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT,
                )
                self.assertTrue(
                    User.objects.filter(
                        pk=old_user_id,
                        email=f"{label}-delete@test.com",
                        is_active=True,
                        **flags,
                    ).exists(),
                )
                log = PrivacyRequestLog.objects.get(pk=result.audit_log_id)
                self.assertEqual(log.request_type, PrivacyRequestLog.REQUEST_DELETE)
                self.assertEqual(log.status, PrivacyRequestLog.STATUS_BLOCKED)
                self.assertEqual(log.old_user_id, old_user_id)
                self.assertEqual(
                    log.blocker_reason,
                    PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT,
                )

    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_active_subscription_is_blocked_and_notifies_staff(self, notify):
        user = User.objects.create_user(
            email="paid-delete@test.com",
            password="TestPass123!",
            tier=self.basic_tier,
            subscription_id="sub_active",
        )
        old_user_id = user.pk

        result = delete_account_for_privacy(
            user,
            {
                "ip": "192.0.2.21",
                "user_agent": "controlled-operator-subscription-guard-test",
            },
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, PrivacyRequestLog.STATUS_BLOCKED)
        self.assertEqual(
            result.blocker_reason,
            PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION,
        )
        self.assertTrue(
            User.objects.filter(
                pk=old_user_id,
                email="paid-delete@test.com",
                tier=self.basic_tier,
                subscription_id="sub_active",
                is_active=True,
            ).exists(),
        )
        log = PrivacyRequestLog.objects.get(pk=result.audit_log_id)
        self.assertEqual(log.request_type, PrivacyRequestLog.REQUEST_DELETE)
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_BLOCKED)
        self.assertEqual(log.old_user_id, old_user_id)
        self.assertEqual(
            log.blocker_reason,
            PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION,
        )
        notify.assert_called_once_with(
            event="blocked_active_subscription",
            email="paid-delete@test.com",
            old_user_id=old_user_id,
            row_count_summary={},
        )


@tag("core")
class PrivacyDeletionSuccessTest(TierSetupMixin, TestCase):
    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_successful_deletion_erases_member_rows_and_invalidates_session(
        self,
        notify,
    ):
        user = User.objects.create_user(
            email="delete-success@test.com",
            password="TestPass123!",
            first_name="Delete",
        )
        user.slack_user_id = "U_DELETE"
        user.save(update_fields=["slack_user_id"])
        course = _course("delete-course")
        Enrollment.objects.create(user=user, course=course)
        event = _event("delete-event")
        EventRegistration.objects.create(event=event, user=user)
        sprint = _sprint("delete-sprint")
        plan = Plan.objects.create(member=user, sprint=sprint, goal="Erase me")
        MemberAPIKey.create_for_user(user=user, name="delete key")
        Notification.objects.create(user=user, title="Delete", body="Soon")
        Comment.objects.create(user=user, content_id=plan.comment_content_id, body="Hi")
        CRMRecord.objects.create(user=user, summary="Private CRM")
        thread = SlackThread.objects.create(
            channel_id="CDEL",
            thread_ts="222.333",
            member=user,
            plan=plan,
            posted_at=timezone.now(),
        )
        SlackMessage.objects.create(
            thread=thread,
            ts="222.333",
            text="private sprint update",
            posted_at=timezone.now(),
            is_root=True,
        )
        retained_thread = SlackThread.objects.create(
            channel_id="CDEL",
            thread_ts="333.444",
            slack_user_id="U_OTHER",
            posted_at=timezone.now(),
            reply_count=1,
        )
        SlackMessage.objects.create(
            thread=retained_thread,
            ts="333.444",
            slack_user_id="U_OTHER",
            text="other member root",
            posted_at=timezone.now(),
            is_root=True,
        )
        SlackMessage.objects.create(
            thread=retained_thread,
            ts="333.445",
            slack_user_id="U_DELETE",
            text="delete my reply",
            posted_at=timezone.now(),
        )
        Project.objects.create(
            title="Published member project",
            slug="published-member-project",
            description="Public",
            date=date(2026, 7, 1),
            author="Delete",
            submitter=user,
            published=True,
            status="published",
        )
        Project.objects.create(
            title="Draft member project",
            slug="draft-member-project",
            description="Private",
            date=date(2026, 7, 2),
            author="Delete",
            submitter=user,
            published=False,
            status="pending_review",
        )

        self.client.force_login(user)
        session_key = self.client.session.session_key
        old_user_id = user.pk
        result = delete_account_for_privacy(
            user,
            {"ip": "127.0.0.1", "user_agent": "controlled-operator-test"},
        )

        self.assertTrue(result.success)
        self.assertFalse(User.objects.filter(email="delete-success@test.com").exists())
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertFalse(
            self.client.login(
                email="delete-success@test.com",
                password="TestPass123!",
            )
        )
        self.assertFalse(Enrollment.objects.filter(course=course).exists())
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assertFalse(Plan.objects.filter(pk=plan.pk).exists())
        self.assertFalse(MemberAPIKey.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(CRMRecord.objects.exists())
        self.assertEqual(SlackThread.objects.count(), 2)
        erased_thread = SlackThread.objects.get(thread_ts="222.333")
        self.assertTrue(erased_thread.privacy_erased)
        self.assertIsNone(erased_thread.member_id)
        self.assertIsNone(erased_thread.plan_id)
        self.assertEqual(erased_thread.slack_user_id, "")
        self.assertEqual(erased_thread.messages.get().text, "")
        retained_thread.refresh_from_db()
        self.assertEqual(retained_thread.reply_count, 1)
        self.assertEqual(retained_thread.messages.count(), 2)
        self.assertFalse(
            SlackMessage.objects.filter(slack_user_id="U_DELETE").exists()
        )
        erased_reply = retained_thread.messages.get(ts="333.445")
        self.assertEqual(erased_reply.text, "")
        self.assertEqual(erased_reply.author_display, "")
        self.assertTrue(Project.objects.filter(slug="published-member-project").exists())
        published = Project.objects.get(slug="published-member-project")
        self.assertIsNone(published.submitter)
        self.assertEqual(published.author, "Deleted member")
        self.assertFalse(Project.objects.filter(slug="draft-member-project").exists())

        log = PrivacyRequestLog.objects.get(request_type="delete")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_COMPLETED)
        self.assertEqual(log.old_user_id, old_user_id)
        self.assertIn("accounts.User", log.row_count_summary["erased"])
        self.assertIn(
            "published_submitted_projects",
            log.row_count_summary["anonymized"],
        )
        notify.assert_called_once()

    @patch("accounts.services.privacy.notify_privacy_staff")
    def test_retains_payment_records_and_scrubs_webhook_payload(self, notify):
        user = User.objects.create_user(email="stripe-delete@test.com")
        old_user_id = user.pk
        user.stripe_customer_id = "cus_delete"
        user.subscription_id = ""
        user.save(update_fields=["stripe_customer_id", "subscription_id"])
        ConversionAttribution.objects.create(
            user=user,
            stripe_session_id="cs_delete",
            stripe_subscription_id="sub_old",
            tier=self.basic_tier,
            billing_period="monthly",
            amount_eur=20,
            mrr_eur=20,
        )
        PaymentAccountMismatch.objects.create(
            stripe_session_id="cs_mismatch",
            stripe_customer_id="cus_delete",
            stripe_subscription_id="sub_old",
            stripe_email="stripe-delete@test.com",
            paid_user=user,
            reason=PaymentAccountMismatch.REASON_UNKNOWN_REFERENCE,
            details={"email": "stripe-delete@test.com", "customer": "cus_delete"},
        )
        WebhookEvent.objects.create(
            stripe_event_id="evt_delete",
            event_type="checkout.session.completed",
            payload={
                "data": {
                    "object": {
                        "customer": "cus_delete",
                        "customer_email": "stripe-delete@test.com",
                    },
                },
            },
        )

        result = delete_account_for_privacy(user, {"ip": "127.0.0.1"})

        self.assertTrue(result.success)
        attribution = ConversionAttribution.objects.get(stripe_session_id="cs_delete")
        self.assertIsNone(attribution.user)
        mismatch = PaymentAccountMismatch.objects.get(stripe_session_id="cs_mismatch")
        self.assertIsNone(mismatch.paid_user)
        self.assertEqual(
            mismatch.stripe_email,
            f"deleted-user-{old_user_id}@privacy.invalid",
        )
        self.assertEqual(mismatch.details["email"], "[privacy-redacted]")
        event = WebhookEvent.objects.get(stripe_event_id="evt_delete")
        payload_text = json.dumps(event.payload)
        self.assertNotIn("stripe-delete@test.com", payload_text)
        self.assertNotIn("cus_delete", payload_text)
        self.assertIn("scrubbed_webhook_events", result.row_count_summary["retained"])
        log = PrivacyRequestLog.objects.get(request_type="delete")
        self.assertEqual(log.status, PrivacyRequestLog.STATUS_COMPLETED)
        self.assertTrue(
            PrivacyRequestLog.objects.filter(pk=log.pk).exists(),
            "PrivacyRequestLog must survive User deletion.",
        )
        notify.assert_called_once()


@tag("core")
class PrivacyRequestLogAdminTest(TestCase):
    def test_staff_can_view_minimal_privacy_request_trail_in_admin(self):
        staff = User.objects.create_superuser(
            email="privacy-admin@test.com",
            password="TestPass123!",
        )
        log = PrivacyRequestLog.objects.create(
            request_type=PrivacyRequestLog.REQUEST_EXPORT,
            status=PrivacyRequestLog.STATUS_COMPLETED,
            old_user_id=12345,
            normalized_email_hash="hash-only",
            email_domain="example.com",
            row_count_summary={"erased": {"sessions": 1}},
            request_ip_hash="ip-hash",
            user_agent_hash="ua-hash",
        )

        self.client.force_login(staff)

        changelist = self.client.get("/admin/accounts/privacyrequestlog/")
        self.assertEqual(changelist.status_code, 200)
        self.assertContains(changelist, "example.com")
        self.assertContains(changelist, "export")
        self.assertNotContains(changelist, "primary_email")

        detail = self.client.get(f"/admin/accounts/privacyrequestlog/{log.pk}/change/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "hash-only")
        self.assertContains(detail, "ip-hash")
        self.assertNotContains(detail, "primary_email")


@tag('core')
class CalendlyPrivacyLifecycleTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='calendly-privacy@test.com')
        EmailAlias.objects.create(user=self.user, email='call-alias@test.com')
        self.log = WebhookLog.objects.create(
            service='calendly', event_type='invitee.created', processed=True,
            payload={
                'event': 'invitee.created',
                'payload': {'email': 'call-alias@test.com', 'name': 'Private Name'},
            },
        )
        self.staged = UnmatchedBookedCall.objects.create(
            member=self.user,
            invitee_email='call-alias@test.com',
            invitee_name='Private Name',
            calendly_event_uri='https://api.calendly.com/events/privacy-stage',
            calendly_invitee_uri='https://api.calendly.com/invitees/privacy-stage',
            scheduling_url='https://calendly.com/unmatched/privacy-stage',
        )

    def test_export_includes_matching_calendly_delivery(self):
        exported = build_user_data_export(self.user)
        rows = exported['events_community']['calendly_webhook_deliveries']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['payload']['payload']['email'], 'call-alias@test.com')
        staged = exported['events_community']['unmatched_booked_calls']
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0]['invitee_email'], 'call-alias@test.com')
        self.assertEqual(staged[0]['invitee_name'], 'Private Name')
        self.assertEqual(
            staged[0]['scheduling_url'],
            'https://calendly.com/unmatched/privacy-stage',
        )

    @patch('accounts.services.privacy.notify_privacy_staff')
    def test_delete_scrubs_primary_and_alias_from_durable_delivery(self, _notify):
        result = delete_account_for_privacy(self.user)
        self.assertTrue(result.success)
        self.log.refresh_from_db()
        self.assertEqual(self.log.payload['payload']['email'], REDACTED)
        self.assertEqual(self.log.payload['payload']['name'], REDACTED)
        self.assertFalse(UnmatchedBookedCall.objects.filter(pk=self.staged.pk).exists())
