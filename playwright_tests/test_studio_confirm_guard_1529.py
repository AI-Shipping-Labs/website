"""Browser coverage for safe Studio destructive confirmations (issue #1529)."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]


def _dialog_controller(page):
    state = {"accept": False, "messages": []}

    def handle(dialog):
        state["messages"].append(dialog.message)
        if state["accept"]:
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", handle)
    return state


def _course(title, slug):
    from django.db import connection

    from content.models import Course

    course = Course.objects.create(title=title, slug=slug, status="published")
    connection.close()
    return course


class TestStudioConfirmGuard:
    @browser_journey
    def test_apostrophe_course_unenroll_cancel_then_accepts_without_script_injection(
        self, django_server, browser
    ):
        from django.db import connection

        from content.models import Enrollment

        title = (
            "Beginner's \"AI\" </form>"
            "<script>window.coursePwned=true</script>"
        )
        create_staff_user("confirm-admin@test.com")
        member = create_user("dave@test.com", tier_slug="main")
        course = _course(title, "confirm-enrollment-1529")
        enrollment = Enrollment.objects.create(
            user=member, course=course, source="admin"
        )
        connection.close()

        context = auth_context(browser, "confirm-admin@test.com")
        page = context.new_page()
        dialogs = _dialog_controller(page)
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )
        assert page.evaluate("window.coursePwned") is None

        button = page.get_by_test_id("unenroll-row-btn")
        button.click()
        assert dialogs["messages"] == [f"Unenroll dave@test.com from {title}?"]
        enrollment.refresh_from_db()
        assert enrollment.unenrolled_at is None
        expect(button).to_be_visible()

        dialogs["accept"] = True
        button.click()
        page.wait_for_load_state("domcontentloaded")
        enrollment.refresh_from_db()
        assert enrollment.unenrolled_at is not None
        assert "Unenrolled dave@test.com" in page.content()
        context.close()

    @browser_journey
    def test_apostrophe_instructor_remove_cancel_then_accepts(
        self, django_server, browser
    ):
        from django.db import connection

        from content.models import CourseInstructor, Instructor

        name = "O'Brien </form><script>window.instructorPwned=true</script>"
        create_staff_user("confirm-admin@test.com")
        course = _course("Instructor confirm", "confirm-instructor-1529")
        instructor = Instructor.objects.create(
            instructor_id="obrien-confirm-1529", name=name
        )
        association = CourseInstructor.objects.create(
            course=course, instructor=instructor, position=0
        )
        connection.close()

        context = auth_context(browser, "confirm-admin@test.com")
        page = context.new_page()
        dialogs = _dialog_controller(page)
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert page.evaluate("window.instructorPwned") is None

        row = page.get_by_test_id("course-instructor-row")
        remove = row.get_by_role("button", name="Remove", exact=True)
        remove.click()
        assert dialogs["messages"] == [f"Remove {name} from this course?"]
        assert CourseInstructor.objects.filter(pk=association.pk).exists()
        expect(row).to_contain_text("O'Brien")

        dialogs["accept"] = True
        remove.click()
        page.wait_for_load_state("domcontentloaded")
        assert not CourseInstructor.objects.filter(pk=association.pk).exists()
        expect(page.get_by_test_id("course-instructors-empty")).to_be_visible()
        context.close()

    @browser_journey
    def test_revoke_access_keeps_dynamic_email_and_existing_post(
        self, django_server, browser
    ):
        from django.db import connection

        from content.models import CourseAccess

        admin = create_staff_user("confirm-admin@test.com")
        member = create_user("member.o'brien@test.com", tier_slug="main")
        course = _course("Access confirm", "confirm-access-1529")
        access = CourseAccess.objects.create(
            user=member,
            course=course,
            access_type="granted",
            granted_by=admin,
        )
        connection.close()

        context = auth_context(browser, "confirm-admin@test.com")
        page = context.new_page()
        dialogs = _dialog_controller(page)
        dialogs["accept"] = True
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )
        page.get_by_test_id("revoke-btn").click()
        page.wait_for_load_state("domcontentloaded")

        assert dialogs["messages"] == [
            "Revoke access for member.o'brien@test.com?"
        ]
        assert not CourseAccess.objects.filter(pk=access.pk).exists()
        assert "Access revoked for member.o'brien@test.com" in page.content()
        context.close()

    @browser_journey
    def test_email_reset_cancels_from_list_and_keyboard_then_accepts_from_edit(
        self, django_server, browser
    ):
        from django.db import connection

        from email_app.models import EmailTemplateOverride

        create_staff_user("confirm-admin@test.com")
        override = EmailTemplateOverride.objects.create(
            template_name="welcome",
            subject="Confirmation override",
            body_markdown="Still here after cancel.",
        )
        connection.close()

        context = auth_context(browser, "confirm-admin@test.com")
        page = context.new_page()
        dialogs = _dialog_controller(page)
        message = (
            "Reset welcome to the filesystem default? This deletes the override."
        )
        page.goto(
            f"{django_server}/studio/email-templates/",
            wait_until="domcontentloaded",
        )
        row = page.locator("tr", has_text="welcome").first
        reset = row.get_by_role("button", name="Reset to default", exact=True)
        reset.focus()
        reset.press("Enter")
        assert dialogs["messages"] == [message]
        assert EmailTemplateOverride.objects.filter(pk=override.pk).exists()
        expect(reset).to_be_visible()

        row.get_by_role("link", name="Edit", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        reset = page.get_by_role("button", name="Reset to default", exact=True)
        reset.click()
        assert dialogs["messages"] == [message, message]
        assert EmailTemplateOverride.objects.filter(pk=override.pk).exists()

        dialogs["accept"] = True
        reset.click()
        page.wait_for_load_state("domcontentloaded")
        assert dialogs["messages"] == [message, message, message]
        assert not EmailTemplateOverride.objects.filter(pk=override.pk).exists()
        expect(page.get_by_text('Reverted "welcome" to the filesystem default.')).to_be_visible()
        context.close()
