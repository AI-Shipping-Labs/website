"""Tests for ``GET /api/users/<email>/course-progress`` (issue #1836)."""

from datetime import timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from analytics.activity import record_lesson_open
from analytics.models import UserActivity
from api.openapi.decorator import OPENAPI_SPEC_ATTR
from api.views.users import user_course_progress
from community.models import CommunityAuditLog
from content.models.course import Course, Module, Unit, UserCourseProgress
from content.services.completion import unmark_completed

User = get_user_model()

FORBIDDEN_KEYS = {
    "body",
    "body_html",
    "homework",
    "homework_html",
    "overview",
    "overview_html",
    "video_url",
    "timestamps",
    "content_hash",
    "password",
    "stripe_customer_id",
    "stripe_product_id",
    "stripe_price_id",
    "slack_user_id",
    "tags",
    "notes",
    "aliases",
    "crm_record",
    "source_repo",
    "source_path",
    "source_commit_sha",
    "source_checksum",
}

SECRET_STRINGS = (
    "SECRET_LESSON_BODY",
    "SECRET_HOMEWORK",
    "SECRET_VIDEO_URL",
    "SECRET_TIMESTAMPS",
    "SECRET_CONTENT_HASH",
    "SECRET_MODULE_OVERVIEW",
    "cus_progress_secret",
    "U_PROGRESS_SLACK",
)


def _keys(value, found):
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(key)
            _keys(child, found)
    elif isinstance(value, list):
        for child in value:
            _keys(child, found)


class UserCourseProgressApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="progress-api-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="progress-api")
        cls.member = User.objects.create_user(
            email="Course.Progress@test.com",
            password="pw",
            first_name="Course",
            last_name="Progress",
        )
        cls.member.stripe_customer_id = "cus_progress_secret"
        cls.member.slack_user_id = "U_PROGRESS_SLACK"
        cls.member.save(update_fields=["stripe_customer_id", "slack_user_id"])
        cls.other = User.objects.create_user(
            email="other.progress@test.com",
            password="pw",
        )
        cls.empty_member = User.objects.create_user(
            email="empty.progress@test.com",
            password="pw",
            first_name="Empty",
            last_name="Member",
        )
        cls.non_staff = User.objects.create_user(
            email="progress-api-nonstaff@test.com",
            password="pw",
        )
        cls.non_staff_token = Token(
            key="progress-api-non-staff-token",
            user=cls.non_staff,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])
        EmailAlias.objects.create(
            user=cls.member,
            email="alias-progress@test.com",
        )

        cls.alpha = Course.objects.create(
            title="Alpha",
            slug="alpha",
            status="published",
        )
        cls.draft = Course.objects.create(
            title="Draft Course",
            slug="draft-course",
            status="draft",
        )
        cls.empty_course = Course.objects.create(
            title="Empty Course",
            slug="empty-course",
            status="published",
        )
        empty_module = Module.objects.create(
            course=cls.empty_course,
            title="Unused",
            slug="unused",
            sort_order=1,
        )
        Unit.objects.create(
            module=empty_module,
            title="Nobody finished this",
            slug="nobody",
            sort_order=1,
            kind="lesson",
        )

        later = Module.objects.create(
            course=cls.alpha, title="Later", slug="later", sort_order=5,
        )
        early = Module.objects.create(
            course=cls.alpha, title="Early", slug="early", sort_order=1,
            overview="SECRET_MODULE_OVERVIEW",
        )
        early_tie = Module.objects.create(
            course=cls.alpha, title="Early Tie", slug="early-tie", sort_order=1,
        )
        parent = Module.objects.create(
            course=cls.alpha, title="Parent", slug="parent", sort_order=3,
        )
        child_late = Module.objects.create(
            course=cls.alpha, title="Child Late", slug="child-late",
            sort_order=2, parent=parent,
        )
        child_early = Module.objects.create(
            course=cls.alpha, title="Bonus Child", slug="bonus-child",
            sort_order=1, parent=parent, is_bonus=True,
        )
        draft_module = Module.objects.create(
            course=cls.draft, title="Draft Week", slug="draft-week", sort_order=1,
        )

        cls.early_second = cls._unit(
            early, "early-second", "Early Second", 2, "homework",
        )
        cls.early_first = cls._unit(
            early, "early-first", "Early First", 1, "lesson",
            body="SECRET_LESSON_BODY",
            homework="SECRET_HOMEWORK",
            video_url="https://example.com/SECRET_VIDEO_URL",
            timestamps=[{"label": "SECRET_TIMESTAMPS"}],
            content_hash="SECRET_CONTENT_HASH",
        )
        cls.tie_unit = cls._unit(early_tie, "tie-unit", "Tie Unit", 1, "event")
        cls.bonus_first = cls._unit(
            child_early, "bonus-first", "Bonus First", 1, "lesson",
        )
        cls.bonus_via_module = cls._unit(
            child_early, "bonus-via-module", "Bonus Via Module", 2,
            "checklist_item", is_bonus=False,
        )
        cls.child_late_unit = cls._unit(
            child_late, "child-late-unit", "Child Late Unit", 1, "event",
        )
        cls.late_unit = cls._unit(later, "late-unit", "Late Unit", 1, "lesson")
        cls.not_completed = cls._unit(
            later, "not-completed", "Not Completed", 2, "lesson",
        )
        cls.other_only = cls._unit(
            later, "other-only", "Other Only", 3, "lesson",
        )
        cls.opened_only = cls._unit(
            later, "opened-only", "Opened Only", 4, "lesson",
        )
        cls.will_unmark = cls._unit(
            later, "will-unmark", "Will Unmark", 5, "lesson",
        )
        cls.draft_unit = cls._unit(
            draft_module, "draft-unit", "Draft Unit", 1, "lesson",
        )

        base = timezone.datetime(2026, 9, 20, 15, 4, tzinfo=dt_timezone.utc)
        ordered = [
            cls.draft_unit,
            cls.late_unit,
            cls.child_late_unit,
            cls.bonus_via_module,
            cls.bonus_first,
            cls.tie_unit,
            cls.early_second,
            cls.early_first,
        ]
        cls.progress = {}
        for offset, unit in enumerate(ordered):
            cls.progress[unit.slug] = UserCourseProgress.objects.create(
                user=cls.member,
                unit=unit,
                completed_at=base + timedelta(minutes=offset),
            )
        UserCourseProgress.objects.create(
            user=cls.member,
            unit=cls.not_completed,
            completed_at=None,
        )
        UserCourseProgress.objects.create(
            user=cls.other,
            unit=cls.other_only,
            completed_at=base,
        )
        opened = record_lesson_open(cls.member, unit=cls.opened_only)
        if opened is None:
            raise RuntimeError("lesson_open row was not recorded")
        cls.syllabus_slugs = [
            "early-first",
            "early-second",
            "tie-unit",
            "bonus-first",
            "bonus-via-module",
            "child-late-unit",
            "late-unit",
            "draft-unit",
        ]

    @classmethod
    def _unit(cls, module, slug, title, sort_order, kind, **extra):
        return Unit.objects.create(
            module=module,
            slug=slug,
            title=title,
            sort_order=sort_order,
            kind=kind,
            **extra,
        )

    def _auth(self, token=None):
        token = token or self.token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _url(self, email=None):
        return f"/api/users/{email or self.member.email}/course-progress"

    def _get(self, email=None, query="", token=None):
        return self.client.get(
            f"{self._url(email)}{query}",
            **self._auth(token),
        )

    def test_lists_only_that_users_completed_rows(self):
        audit_before = CommunityAuditLog.objects.count()
        response = self._get(email="course.progress@TEST.com")
        body = response.json()
        self.assertEqual(
            body["user"],
            {
                "email": "Course.Progress@test.com",
                "display_name": "Course Progress",
            },
        )
        self.assertEqual(body["count"], len(body["completions"]))
        self.assertEqual(body["count"], len(self.syllabus_slugs))
        slugs = [item["unit"]["slug"] for item in body["completions"]]
        self.assertEqual(slugs, self.syllabus_slugs)
        kinds = {item["unit"]["kind"] for item in body["completions"]}
        self.assertEqual(
            kinds,
            {"lesson", "homework", "event", "checklist_item"},
        )
        self.assertIn("draft-unit", slugs)
        self.assertNotIn("not-completed", slugs)
        self.assertNotIn("other-only", slugs)
        self.assertNotIn("opened-only", slugs)
        self.assertTrue(
            UserActivity.objects.filter(
                user=self.member,
                event_type=UserActivity.EVENT_LESSON_OPEN,
                object_id=str(self.opened_only.pk),
            ).exists(),
        )
        rendered = response.content.decode()
        for secret in SECRET_STRINGS:
            self.assertNotIn(secret, rendered)
        found = set()
        _keys(body, found)
        self.assertFalse(found & FORBIDDEN_KEYS)
        for item in body["completions"]:
            self.assertIsNotNone(item["completed_at"])
        self.assertEqual(CommunityAuditLog.objects.count(), audit_before)

    def test_shape_parent_url_bonus_and_timestamp(self):
        body = self._get().json()
        by_slug = {item["unit"]["slug"]: item for item in body["completions"]}

        early = by_slug["early-first"]
        self.assertEqual(early["id"], self.progress["early-first"].pk)
        self.assertEqual(
            early["completed_at"],
            self.progress["early-first"].completed_at.isoformat(),
        )
        self.assertEqual(
            early["course"],
            {"id": self.alpha.pk, "slug": "alpha", "title": "Alpha"},
        )
        self.assertEqual(
            set(early["module"]),
            {"id", "slug", "title", "sort_order", "parent"},
        )
        self.assertIsNone(early["module"]["parent"])
        self.assertEqual(early["module"]["slug"], "early")
        self.assertEqual(
            set(early["unit"]),
            {
                "id", "slug", "title", "kind", "sort_order",
                "is_bonus", "url",
            },
        )
        self.assertFalse(early["unit"]["is_bonus"])
        self.assertEqual(early["unit"]["url"], self.early_first.get_absolute_url())

        bonus = by_slug["bonus-via-module"]
        self.assertFalse(self.bonus_via_module.is_bonus)
        self.assertTrue(self.bonus_via_module.effective_is_bonus)
        self.assertTrue(bonus["unit"]["is_bonus"])
        self.assertEqual(
            bonus["module"]["parent"],
            {
                "id": self.bonus_via_module.module.parent_id,
                "slug": "parent",
                "title": "Parent",
                "sort_order": 3,
            },
        )
        self.assertEqual(
            bonus["unit"]["url"],
            self.bonus_via_module.get_absolute_url(),
        )
        self.assertIn("/parent/bonus-child/", bonus["unit"]["url"])

        draft = by_slug["draft-unit"]
        self.assertEqual(draft["course"]["slug"], "draft-course")
        self.assertEqual(self.draft.status, "draft")

    def test_kind_and_course_filters(self):
        homework = self._get(query="?kind=HOMEWORK")
        self.assertEqual(
            [item["unit"]["slug"] for item in homework.json()["completions"]],
            ["early-second"],
        )

        checklist = self._get(query="?kind=%20checklist_item%20")
        self.assertEqual(
            [item["unit"]["slug"] for item in checklist.json()["completions"]],
            ["bonus-via-module"],
        )

        alpha = self._get(query="?course=alpha")
        slugs = [item["unit"]["slug"] for item in alpha.json()["completions"]]
        self.assertNotIn("draft-unit", slugs)
        self.assertIn("early-first", slugs)

        draft = self._get(query="?course=draft-course")
        self.assertEqual(
            [item["unit"]["slug"] for item in draft.json()["completions"]],
            ["draft-unit"],
        )

        empty = self._get(query="?course=empty-course")
        self.assertEqual(empty.json()["count"], 0)
        self.assertEqual(empty.json()["completions"], [])

        blank = self._get(query="?course=&kind=")
        self.assertEqual(
            [item["unit"]["slug"] for item in blank.json()["completions"]],
            self.syllabus_slugs,
        )

        missing = self._get(query="?course=missing-course")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json(),
            {"error": "Course not found", "code": "course_not_found"},
        )

        invalid = self._get(query="?kind=nope")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.json(),
            {
                "error": "Invalid unit kind: 'nope'",
                "code": "validation_error",
                "details": {
                    "field": "kind",
                    "value": "nope",
                    "allowed": [
                        "lesson",
                        "homework",
                        "event",
                        "checklist_item",
                    ],
                },
            },
        )

    def test_unmark_removes_the_unit(self):
        UserCourseProgress.objects.create(
            user=self.member,
            unit=self.will_unmark,
            completed_at=timezone.now(),
        )
        self.assertTrue(unmark_completed(self.member, self.will_unmark))
        slugs = [
            item["unit"]["slug"]
            for item in self._get().json()["completions"]
        ]
        self.assertNotIn("will-unmark", slugs)
        self.assertFalse(
            UserCourseProgress.objects.filter(
                user=self.member, unit=self.will_unmark,
            ).exists(),
        )

    def test_unknown_email_and_alias_are_not_found(self):
        missing = self._get(email="nobody-progress@test.com")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json(),
            {"error": "User not found", "code": "user_not_found"},
        )
        alias = self._get(email="alias-progress@test.com")
        self.assertEqual(alias.status_code, 404)
        self.assertEqual(alias.json()["code"], "user_not_found")

    def test_auth_required(self):
        missing = self.client.get(self._url())
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(
            missing.json(),
            {"error": "Authentication token required"},
        )
        rejected = self._get(token=self.non_staff_token)
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.json(), {"error": "Invalid token"})

    def test_writes_are_rejected_and_do_not_change_rows(self):
        before = list(
            UserCourseProgress.objects.order_by("pk").values_list(
                "pk", "user_id", "unit_id", "completed_at",
            ),
        )
        for method in ("post", "patch", "delete"):
            response = getattr(self.client, method)(
                self._url(),
                data="{}",
                content_type="application/json",
                **self._auth(),
            )
            self.assertEqual(response.status_code, 405, method)
        after = list(
            UserCourseProgress.objects.order_by("pk").values_list(
                "pk", "user_id", "unit_id", "completed_at",
            ),
        )
        self.assertEqual(after, before)

    def test_view_declares_openapi_spec(self):
        spec = getattr(user_course_progress, OPENAPI_SPEC_ATTR)
        self.assertEqual(spec["tag"], "Users")
        self.assertIn("GET", spec["methods"])
