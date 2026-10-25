"""Safe Studio destructive-action confirmation markup for issue #1529."""

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from content.models import Course, CourseAccess, CourseInstructor, Enrollment, Instructor

User = get_user_model()


class _FormAttributeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms.append(dict(attrs))
        elif tag == "button":
            self.buttons.append(dict(attrs))


def _confirm_forms(response):
    parser = _FormAttributeParser()
    parser.feed(response.content.decode())
    return [form for form in parser.forms if "data-confirm" in form]


@tag("core")
class StudioConfirmGuardMarkupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-confirm@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member.o'brien@test.com", password="pw",
        )
        cls.course = Course.objects.create(
            title=(
                "Beginner's \"AI\" </form>"
                "<script>window.coursePwned=true</script>"
            ),
            slug="beginners-ai-confirm",
            status="published",
        )
        cls.enrollment = Enrollment.objects.create(
            user=cls.member, course=cls.course, source="admin",
        )
        cls.access = CourseAccess.objects.create(
            user=cls.member, course=cls.course,
            access_type="granted", granted_by=cls.staff,
        )
        cls.instructor = Instructor.objects.create(
            instructor_id="obrien-confirm",
            name="O'Brien </form><script>window.instructorPwned=true</script>",
        )
        cls.association = CourseInstructor.objects.create(
            course=cls.course, instructor=cls.instructor, position=0,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def assert_safe_confirm_forms(self, response, expected_messages):
        forms = _confirm_forms(response)
        self.assertEqual(
            [form["data-confirm"] for form in forms],
            expected_messages,
        )
        self.assertTrue(all("onsubmit" not in form for form in forms))

    def test_studio_base_loads_shared_external_confirm_guard(self):
        response = self.client.get(reverse("studio_dashboard"))

        self.assertContains(response, 'src="/static/js/studio/confirm_guard.js"')

    def test_enrollment_confirm_messages_are_safe_on_desktop_and_mobile(self):
        response = self.client.get(
            reverse("studio_course_enrollment_list", args=[self.course.pk])
        )
        message = f"Unenroll {self.member.email} from {self.course.title}?"

        self.assert_safe_confirm_forms(response, [message])
        self.assertNotContains(response, "confirm('Unenroll")
        self.assertNotContains(response, "window.coursePwned=true</script>")

    def test_access_confirm_messages_are_safe_on_desktop_and_mobile(self):
        response = self.client.get(
            reverse("studio_course_access_list", args=[self.course.pk])
        )
        message = f"Revoke access for {self.member.email}?"

        self.assert_safe_confirm_forms(response, [message])
        self.assertNotContains(response, "confirm('Revoke")

    def test_instructor_confirm_message_keeps_apostrophe_without_inline_script(self):
        response = self.client.get(reverse("studio_course_edit", args=[self.course.pk]))
        message = f"Remove {self.instructor.name} from this course?"

        matching = [
            form for form in _confirm_forms(response)
            if form["data-confirm"] == message
        ]
        self.assertEqual(len(matching), 1)
        self.assertNotIn("onsubmit", matching[0])
        self.assertNotContains(response, "confirm('Remove O")
        self.assertNotContains(response, "window.instructorPwned=true</script>")
