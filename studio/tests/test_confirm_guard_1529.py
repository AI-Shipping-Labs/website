"""Safe Studio destructive-action confirmation markup for issue #1529."""

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

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
    pass

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

    def test_email_template_reset_uses_safe_form_and_submit_control_markup(self):
        list_response = self.client.get(reverse("studio_email_template_list"))
        message = "Reset welcome to the filesystem default? This deletes the override."
        self.assert_safe_confirm_forms(list_response, [message])
        self.assertNotContains(list_response, "confirm('Reset welcome")

        edit_response = self.client.get(
            reverse("studio_email_template_edit", args=["welcome"])
        )
        parser = _FormAttributeParser()
        parser.feed(edit_response.content.decode())
        reset_controls = [
            button for button in parser.buttons
            if button.get("data-confirm") == message
        ]
        self.assertEqual(len(reset_controls), 1)
        self.assertNotIn("onclick", reset_controls[0])
        self.assertNotContains(edit_response, "confirm('Reset welcome")
