"""Studio surfaces for the structured ``User.bounce_state`` field (issue #766).

Covers the three Studio touchpoints introduced by the refactor:

- ``user_list`` ``?bounce=<state>`` filter (queryset assertion).
- ``_row_tooltip`` adding the bounce line for non-``none`` rows.
- ``user_detail`` rendering the "Bounce status" card (and dropping it
  when the user has no bounce).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from studio.views import users as users_view

User = get_user_model()
FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListBounceFilterTest(TestCase):
    """``?bounce=<state>`` narrows the list to rows with that bounce state."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.clean = User.objects.create_user(
            email="clean@test.com", password="pw",
        )
        cls.soft = User.objects.create_user(
            email="soft@test.com",
            password="pw",
            bounce_state=User.BounceState.SOFT,
            bounce_recorded_at=timezone.now() - timedelta(hours=2),
        )
        cls.perm = User.objects.create_user(
            email="perm@test.com",
            password="pw",
            bounce_state=User.BounceState.PERMANENT,
            bounce_recorded_at=timezone.now() - timedelta(days=1),
        )

    def setUp(self):
        self.client.login(email="staff@test.com", password="pw")

    def _emails(self, response):
        return {row["email"] for row in response.context["page"].object_list}

    def test_filter_permanent_returns_only_permanent_rows(self):
        response = self.client.get("/studio/users/", {"bounce": "permanent"})

        emails = self._emails(response)
        self.assertEqual(emails, {"perm@test.com"})
        self.assertNotIn("soft@test.com", emails)
        self.assertNotIn("clean@test.com", emails)

    def test_filter_soft_returns_only_soft_rows(self):
        response = self.client.get("/studio/users/", {"bounce": "soft"})

        emails = self._emails(response)
        self.assertEqual(emails, {"soft@test.com"})
        self.assertNotIn("perm@test.com", emails)

    def test_filter_any_returns_all_bounce_states(self):
        response = self.client.get("/studio/users/", {"bounce": "any"})

        emails = self._emails(response)
        self.assertIn("clean@test.com", emails)
        self.assertIn("soft@test.com", emails)
        self.assertIn("perm@test.com", emails)

    def test_unknown_bounce_value_falls_back_to_any(self):
        response = self.client.get("/studio/users/", {"bounce": "not-a-state"})

        # The view should treat unknown filters as ``any`` (default).
        self.assertEqual(response.context["bounce_filter"], "any")

    def test_filtered_queryset_helper_applies_bounce_state(self):
        # Exercise ``_filtered_user_queryset`` directly so the contract
        # is locked at the function boundary, not just the view.
        qs = users_view._filtered_user_queryset(
            active_filter="all",
            search="",
            tag_filter="",
            slack_filter="any",
            bounce_filter="permanent",
        )
        emails = set(qs.values_list("email", flat=True))
        self.assertEqual(emails, {"perm@test.com"})


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserRowTooltipBounceLineTest(TestCase):
    """The per-row tooltip appends ``Bounce: <state> (<date>)`` when set."""

    def test_tooltip_appends_bounce_state_for_permanent_user(self):
        recorded_at = timezone.now().replace(microsecond=0)
        user = User.objects.create_user(
            email="bouncer@test.com",
            password="pw",
            bounce_state=User.BounceState.PERMANENT,
            bounce_recorded_at=recorded_at,
        )

        tooltip = users_view._row_tooltip(user, slack_status="Never checked")

        date_part = recorded_at.date().isoformat()
        self.assertIn(f"Bounce: permanent ({date_part})", tooltip)
        # Existing newsletter line still present so we know we APPENDED
        # rather than replaced the tooltip body.
        self.assertIn("Newsletter:", tooltip)
        self.assertIn("Slack workspace:", tooltip)

    def test_tooltip_omits_bounce_line_for_clean_user(self):
        user = User.objects.create_user(email="clean2@test.com", password="pw")
        tooltip = users_view._row_tooltip(user, slack_status="Never checked")

        self.assertNotIn("Bounce:", tooltip)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserDetailBounceCardTest(TestCase):
    """``user_detail`` renders the deliverability card for every user."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff2@test.com", password="pw", is_staff=True,
        )

    def setUp(self):
        self.client.login(email="staff2@test.com", password="pw")

    def test_detail_shows_bounce_card_for_permanent_user(self):
        recorded_at = timezone.now().replace(microsecond=0)
        user = User.objects.create_user(
            email="dead@test.com",
            password="pw",
            bounce_state=User.BounceState.PERMANENT,
            bounce_recorded_at=recorded_at,
            last_bounce_diagnostic="550 5.1.1 Mailbox does not exist",
        )

        response = self.client.get(
            reverse("studio_user_detail", args=[user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deliverability")
        # Human-readable label, not the slug.
        self.assertContains(response, "Permanent bounce")
        # Diagnostic surfaces verbatim for operator triage.
        self.assertContains(response, "550 5.1.1 Mailbox does not exist")
        # Section is keyed by data-testid so Playwright + future tests
        # can target it without inspecting full HTML.
        self.assertContains(response, "user-detail-deliverability-section")

    def test_detail_shows_clean_deliverability_card_for_clean_user(self):
        user = User.objects.create_user(
            email="happy@test.com", password="pw",
        )

        response = self.client.get(
            reverse("studio_user_detail", args=[user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "user-detail-deliverability-section")
        self.assertContains(response, 'data-bounce-state="none"')
        self.assertContains(response, "No bounce")

    def test_detail_soft_state_renders_label_not_permanent(self):
        user = User.objects.create_user(
            email="softie@test.com",
            password="pw",
            bounce_state=User.BounceState.SOFT,
            bounce_recorded_at=timezone.now() - timedelta(hours=2),
            last_bounce_diagnostic="421 4.4.5 Server busy",
        )

        response = self.client.get(
            reverse("studio_user_detail", args=[user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deliverability")
        # The label "Soft bounce" appears; no stale "permanent" state badge
        # turns up. Assert on the badge's ``data-bounce-state`` attribute
        # rather than the bare word "Permanent" so the State help tooltip
        # (issue #924), which legitimately mentions "Permanent ... bounces",
        # does not trip a substring match.
        self.assertContains(response, "Soft bounce")
        self.assertContains(response, 'data-bounce-state="soft"')
        self.assertNotContains(response, 'data-bounce-state="permanent"')
        self.assertContains(response, "421 4.4.5 Server busy")
