import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.urls import reverse

from content.models import Course, CourseInstructor, Instructor, MarketingPage, Workshop
from content.services import course_instructors as instructor_service
from content.services.course_instructors import CourseInstructorError
from integrations.models import UtmCampaign, UtmCampaignLink
from studio.views.email_templates import TEMPLATE_SENT_WHEN, _all_template_names

User = get_user_model()


class ContentPolishStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="studio-1292@example.com", password="pw", is_staff=True
        )
        cls.course = Course.objects.create(title="Studio course", slug="studio-course")
        cls.source_course = Course.objects.create(
            title="Source Studio course", slug="source-studio-course", source_repo="owner/repo"
        )
        cls.ada = Instructor.objects.create(instructor_id="ada-studio", name="Ada Studio")
        cls.grace = Instructor.objects.create(instructor_id="grace-studio", name="Grace Studio")
        cls.workshop = Workshop.objects.create(
            content_id=uuid.uuid4(), slug="studio-preview", title="Studio preview",
            date=date(2026, 7, 18), status="draft"
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_every_editable_email_template_has_trigger_guidance(self):
        names = _all_template_names()
        self.assertTrue(names)
        self.assertEqual(set(names), set(TEMPLATE_SENT_WHEN))
        response = self.client.get(reverse("studio_email_template_list"))
        self.assertEqual(response.status_code, 200)
        for row in response.context["rows"]:
            self.assertTrue(row["sent_when"].strip())
        self.assertContains(response, "Sent when")
        template_name = names[0]
        expected = TEMPLATE_SENT_WHEN[template_name]
        edit_url = reverse("studio_email_template_edit", args=[template_name])
        response = self.client.get(edit_url)
        self.assertEqual(response.context["sent_when"], expected)
        self.assertContains(response, expected)
        response = self.client.post(
            edit_url, {"subject": "", "body_markdown": "", "footer_note": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sent_when"], expected)
        self.assertContains(response, expected)

    def test_course_instructors_add_reorder_remove_and_source_ownership(self):
        add_url = reverse("studio_course_instructor_add", args=[self.course.pk])
        self.client.post(add_url, {"instructor_id": "ada-studio", "position": "0"})
        self.client.post(add_url, {"instructor_id": "grace-studio", "position": "0"})
        rows = list(CourseInstructor.objects.filter(course=self.course).order_by("position"))
        self.assertEqual([r.instructor_id for r in rows], [self.grace.pk, self.ada.pk])
        response = self.client.post(
            reverse("studio_course_instructor_reorder", args=[self.course.pk]),
            {"association_id": [str(r.pk) for r in rows], "position": ["2", "1"]},
        )
        self.assertRedirects(
            response, reverse("studio_course_edit", args=[self.course.pk]) + "#instructors",
            fetch_redirect_response=False,
        )
        rows = list(CourseInstructor.objects.filter(course=self.course).order_by("position"))
        self.assertEqual([r.position for r in rows], [0, 1])
        response = self.client.post(
            reverse("studio_course_instructor_remove", args=[self.course.pk, rows[0].pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CourseInstructor.objects.filter(course=self.course).count(), 1)
        response = self.client.post(
            reverse("studio_course_instructor_add", args=[self.source_course.pk]),
            {"instructor_id": "ada-studio", "position": "0"},
        )
        self.assertEqual(response.status_code, 409)

    def test_course_instructor_invalid_stale_cross_course_and_last_removal(self):
        other = Course.objects.create(title="Other", slug="other-1292")
        foreign = CourseInstructor.objects.create(
            course=other, instructor=self.ada, position=0
        )
        local = CourseInstructor.objects.create(
            course=self.course, instructor=self.grace, position=0
        )
        before = list(CourseInstructor.objects.filter(course=self.course).values_list("pk", "position"))
        response = self.client.post(
            reverse(
                "studio_course_instructor_remove",
                args=[self.course.pk, foreign.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(CourseInstructor.objects.filter(course=self.course).values_list("pk", "position")),
            before,
        )
        response = self.client.post(
            reverse("studio_course_instructor_reorder", args=[self.course.pk]),
            {"association_id": [str(local.pk), str(foreign.pk)], "position": ["0", "-1"]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CourseInstructor.objects.get(pk=local.pk).position, 0)
        response = self.client.post(
            reverse("studio_course_instructor_remove", args=[self.course.pk, local.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CourseInstructor.objects.filter(course=self.course).exists())

    def test_course_instructor_routes_are_staff_post_and_csrf_only(self):
        member = User.objects.create_user(email="nonstaff-1292@example.com", password="pw")
        url = reverse("studio_course_instructor_add", args=[self.course.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(member)
        self.assertEqual(self.client.post(url, {"instructor_id": "ada-studio", "position": "0"}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(
            csrf_client.post(url, {"instructor_id": "ada-studio", "position": "0"}).status_code,
            403,
        )

    def test_incremental_mutations_lock_course_before_reading_associations(self):
        original_lock = instructor_service._lock_database_course
        original_read = instructor_service._ordered_rows
        calls = []

        def record_lock(course):
            calls.append("lock")
            return original_lock(course)

        def record_read(course):
            calls.append("read")
            return original_read(course)

        with patch.object(
            instructor_service,
            "_lock_database_course",
            side_effect=record_lock,
        ), patch.object(instructor_service, "_ordered_rows", side_effect=record_read):
            instructor_service.add_course_instructor(self.course, self.ada.instructor_id, 0)

        self.assertEqual(calls[:2], ["lock", "read"])
        ada_row = CourseInstructor.objects.get(course=self.course)
        calls.clear()
        with patch.object(
            instructor_service,
            "_lock_database_course",
            side_effect=record_lock,
        ), patch.object(instructor_service, "_ordered_rows", side_effect=record_read):
            instructor_service.remove_course_instructor(self.course, ada_row.pk)
        self.assertEqual(calls[:2], ["lock", "read"])

        ada_row = CourseInstructor.objects.create(
            course=self.course, instructor=self.ada, position=0
        )
        grace_row = CourseInstructor.objects.create(
            course=self.course, instructor=self.grace, position=1
        )
        calls.clear()
        with patch.object(
            instructor_service,
            "_lock_database_course",
            side_effect=record_lock,
        ), patch.object(instructor_service, "_ordered_rows", side_effect=record_read):
            instructor_service.reorder_course_instructors(
                self.course,
                [ada_row.pk, grace_row.pk],
                [1, 0],
            )
        self.assertEqual(calls[:2], ["lock", "read"])

    def test_incremental_add_preserves_a_competing_committed_success(self):
        CourseInstructor.objects.create(
            course=self.course,
            instructor=self.grace,
            position=0,
        )
        instructor_service.add_course_instructor(self.course, self.ada.instructor_id, 0)

        self.assertEqual(
            list(
                CourseInstructor.objects.filter(course=self.course)
                .order_by("position")
                .values_list("instructor__instructor_id", flat=True)
            ),
            [self.ada.instructor_id, self.grace.instructor_id],
        )

    def test_stale_reorder_cannot_resurrect_concurrently_removed_association(self):
        ada_row = CourseInstructor.objects.create(
            course=self.course, instructor=self.ada, position=0
        )
        grace_row = CourseInstructor.objects.create(
            course=self.course, instructor=self.grace, position=1
        )
        CourseInstructor.objects.filter(pk=grace_row.pk).delete()
        with self.assertRaises(CourseInstructorError):
            instructor_service.reorder_course_instructors(
                self.course,
                [ada_row.pk, grace_row.pk],
                [1, 0],
            )

        self.assertEqual(
            list(
                CourseInstructor.objects.filter(course=self.course)
                .values_list("pk", flat=True)
            ),
            [ada_row.pk],
        )

    def test_course_form_has_programmatic_instructor_controls(self):
        response = self.client.get(reverse("studio_course_edit", args=[self.course.pk]))
        self.assertContains(response, 'id="instructors"')
        self.assertContains(response, 'for="course-instructor-add-id"')
        self.assertContains(response, 'for="course-instructor-add-position"')

    def test_workshop_preview_link_and_rotation(self):
        detail = reverse("studio_workshop_detail", args=[self.workshop.pk])
        response = self.client.get(detail)
        self.assertContains(response, self.workshop.get_preview_url())
        old_token = self.workshop.preview_token
        response = self.client.post(
            reverse("studio_workshop_regenerate_preview_token", args=[self.workshop.pk])
        )
        self.assertRedirects(response, detail, fetch_redirect_response=False)
        self.workshop.refresh_from_db()
        self.assertNotEqual(self.workshop.preview_token, old_token)

    def test_published_workshop_detail_uses_canonical_public_action_and_copy(self):
        self.workshop.status = "published"
        self.workshop.save(update_fields=["status"])
        response = self.client.get(
            reverse("studio_workshop_detail", args=[self.workshop.pk])
        )
        self.assertContains(response, 'data-testid="workshop-public-link-panel"')
        self.assertContains(response, 'data-testid="workshop-public-open"')
        self.assertContains(response, "View on site")
        self.assertContains(response, self.workshop.get_absolute_url())
        self.assertNotContains(response, "Anyone with this private link")
        self.assertNotContains(response, ">Preview draft<")

    def test_create_forms_expose_expected_prefills_and_labels(self):
        response = self.client.get(reverse("studio_utm_campaign_create"))
        self.assertEqual(response.context["form_data"]["default_utm_source"], "newsletter")
        self.assertContains(response, "utm_source (required)")
        self.assertContains(response, 'for="utm-campaign-default-source"')
        self.assertContains(response, 'id="utm-campaign-default-source"')
        self.assertContains(response, 'data-create-prefill="true"')
        response = self.client.get(reverse("studio_marketing_page_new"))
        self.assertContains(response, 'for="marketing-page-title"')
        self.assertContains(response, 'for="marketing-page-public-path"')

    def test_utm_error_edit_and_locked_slug_preserve_values(self):
        response = self.client.post(
            reverse("studio_utm_campaign_create"),
            {
                "name": "Manual value",
                "slug": "manual_value",
                "default_utm_source": "custom-source",
                "default_utm_medium": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form_data"]["slug"], "manual_value")
        self.assertEqual(response.context["form_data"]["default_utm_source"], "custom-source")
        campaign = UtmCampaign.objects.create(
            name="Existing", slug="existing_1292", default_utm_source="social",
            default_utm_medium="post", created_by=self.staff,
        )
        UtmCampaignLink.objects.create(
            campaign=campaign, utm_content="first", destination="/first", created_by=self.staff
        )
        response = self.client.get(reverse("studio_utm_campaign_edit", args=[campaign.pk]))
        self.assertTrue(response.context["slug_locked"])
        self.assertEqual(response.context["form_data"]["slug"], "existing_1292")
        self.assertNotContains(response, 'data-create-prefill="true"')

    def test_marketing_error_edit_source_and_reserved_path_preserve_values(self):
        existing = MarketingPage.objects.create(title="Existing", public_path="/existing-1292")
        response = self.client.post(
            reverse("studio_marketing_page_new"),
            {"title": "Manual", "public_path": "/existing-1292", "nav_order": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].public_path, "/existing-1292")
        self.assertIn("public_path", response.context["errors"])
        response = self.client.post(
            reverse("studio_marketing_page_new"),
            {"title": "Reserved", "public_path": "/studio/nope", "nav_order": "0"},
        )
        self.assertIn("public_path", response.context["errors"])
        response = self.client.get(reverse("studio_marketing_page_edit", args=[existing.pk]))
        self.assertNotContains(response, 'data-create-prefill="true"')
        existing.source_repo = "owner/repo"
        existing.save(update_fields=["source_repo"])
        response = self.client.get(reverse("studio_marketing_page_edit", args=[existing.pk]))
        self.assertTrue(response.context["is_synced"])
        self.assertNotContains(response, 'data-create-prefill="true"')

    def test_campaign_recount_requires_staff_post_and_returns_only_count(self):
        url = reverse("studio_campaign_recount")
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"target_min_level": "0", "slack_filter": "any", "audience_verification": "verified_only"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"recipient_count"})
        response = self.client.post(url, {"target_min_level": "999"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_audience")

    def test_campaign_recount_auth_csrf_and_all_malformed_filters_fail_closed(self):
        url = reverse("studio_campaign_recount")
        self.client.logout()
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        member = User.objects.create_user(email="recount-member-1292@example.com", password="pw")
        self.client.force_login(member)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url, {}).status_code, 403)
        self.client.force_login(self.staff)
        for payload in (
            {"target_min_level": "bad"},
            {"slack_filter": "bad"},
            {"audience_verification": "bad"},
            {"target_event": "bad"},
            {"target_event": "999999"},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_audience")
                self.assertNotIn("email", str(response.json()).lower())

    @tag("visual_regression")
    def test_course_and_workshop_table_width_contracts(self):
        response = self.client.get(reverse("studio_course_list"))
        self.assertContains(response, 'whitespace-nowrap')
        self.assertContains(response, "View")
        self.assertContains(response, "Edit")
        response = self.client.get(reverse("studio_workshop_list"))
        self.assertContains(response, 'class="min-w-64')
        self.assertContains(response, 'whitespace-nowrap')
        self.assertContains(response, "Studio preview")
