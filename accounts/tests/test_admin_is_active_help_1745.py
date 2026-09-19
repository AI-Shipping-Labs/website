"""The Django-admin ``Active`` checkbox explains what it destroys (issue #1745).

The Django admin user change form is one of exactly two operator-reachable
paths that set ``is_active = False``; the merge plan is the other. The checkbox
used to say nothing, so a superuser could revoke every credential an account
owns without a hint that anything but a login was being switched off.

These tests hold the help text to three things: it is present on the change
form, it lives on the FORM rather than ``User.is_active`` (a model-level
``help_text`` would generate a migration and leak operator-only wording into
every other form bound to that field), and it is TRUE -- saving the form with
``Active`` unchecked really does kill all three credential families, and
re-checking the box really does not bring them back.
"""

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.admin.user import IS_ACTIVE_HELP_TEXT
from accounts.models import MemberAPIKey, Token

User = get_user_model()


class AdminIsActiveHelpTextTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            email="root-1745@test.com", password="pw"
        )
        cls.member = User.objects.create_user(
            email="key-owner-1745@test.com", password="pw"
        )

    def _change_url(self, user):
        return reverse("admin:accounts_user_change", args=[user.pk])

    def _form_data(self, user, *, active):
        """The change form's required fields, plus the checkbox under test."""
        joined = timezone.localtime(user.date_joined)
        data = {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "import_source": user.import_source,
            "date_joined_0": joined.strftime("%Y-%m-%d"),
            "date_joined_1": joined.strftime("%H:%M:%S"),
        }
        if active:
            data["is_active"] = "on"
        return data

    def test_change_form_explains_what_unchecking_active_destroys(self):
        self.client.force_login(self.superuser)

        response = self.client.get(self._change_url(self.member))

        form = response.context["adminform"].form
        self.assertEqual(form.fields["is_active"].help_text, IS_ACTIVE_HELP_TEXT)
        self.assertContains(response, "operator tokens are deleted")
        self.assertContains(response, "re-checking this box does not restore them")

    def test_help_text_lives_on_the_form_not_the_user_model(self):
        # A model-level help_text would mean a migration and would show this
        # operator-only wording on every other form bound to ``is_active``.
        model_help = User._meta.get_field("is_active").help_text
        self.assertNotIn("replacement key", model_help)
        self.assertNotEqual(model_help, IS_ACTIVE_HELP_TEXT)

    def test_saving_with_active_unchecked_kills_every_credential(self):
        self.client.force_login(self.superuser)
        _, member_plaintext = MemberAPIKey.create_for_user(
            user=self.member, name="member key"
        )
        _, package_plaintext = APIKey.create_for_user(
            user=self.member,
            name="package key",
            scopes=["users.read"],
            kind=APIKey.Kind.MEMBER,
        )
        self.member.is_staff = True
        self.member.save(update_fields=["is_staff"])
        _, token_plaintext = Token.create_for_user(
            user=self.member, name="operator token"
        )
        self.member.is_staff = False
        self.member.save(update_fields=["is_staff"])

        deactivate = self.client.post(
            self._change_url(self.member),
            self._form_data(self.member, active=False),
        )
        self.assertRedirects(
            deactivate, reverse("admin:accounts_user_changelist")
        )
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)

        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNone(APIKey.authenticate(package_plaintext))
        self.assertIsNone(Token.authenticate(token_plaintext))
        self.assertFalse(Token.objects.filter(user=self.member).exists())

        # The help text promises the box is not an undo. Prove it.
        reactivate = self.client.post(
            self._change_url(self.member),
            self._form_data(self.member, active=True),
        )
        self.assertRedirects(
            reactivate, reverse("admin:accounts_user_changelist")
        )
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

        self.assertIsNone(MemberAPIKey.authenticate(member_plaintext))
        self.assertIsNone(APIKey.authenticate(package_plaintext))
        self.assertIsNone(Token.authenticate(token_plaintext))
