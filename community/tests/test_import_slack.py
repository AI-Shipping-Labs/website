from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django_q.models import Schedule

from accounts.models import IMPORT_SOURCE_SLACK, ImportBatch, TierOverride
from accounts.services.import_users import get_import_adapter, run_import_batch
from community.services.import_slack import (
    register_slack_import_adapter,
    slack_workspace_import_adapter,
)
from community.services.slack import SlackAPIError
from email_app.models import EmailLog
from email_app.tasks.welcome_imported import send_imported_welcome_email
from integrations.config import clear_config_cache

User = get_user_model()


def member(slack_id, email, **overrides):
    profile = {
        "email": email,
        "real_name_normalized": overrides.pop("real_name", "Ada Lovelace"),
        "display_name_normalized": overrides.pop("display_name", "ada"),
    }
    profile.update(overrides.pop("profile", {}))
    data = {
        "id": slack_id,
        "team_id": "T123",
        "name": overrides.pop("name", "ada"),
        "profile": profile,
        "tz": "Europe/Berlin",
    }
    data.update(overrides)
    return data


def users_page(members, cursor=""):
    return {
        "ok": True,
        "members": members,
        "response_metadata": {"next_cursor": cursor},
    }


@override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN="xoxb-test")
class SlackImportAdapterTest(TestCase):
    def setUp(self):
        clear_config_cache()
        register_slack_import_adapter()

    def tearDown(self):
        clear_config_cache()

    def test_slack_source_is_registered(self):
        self.assertIs(get_import_adapter(IMPORT_SOURCE_SLACK), slack_workspace_import_adapter)

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_command_dry_run_resolves_registered_adapter_and_reports_counts(self, mock_api):
        mock_api.return_value = users_page([member("U1", "dry@example.com")])
        out = StringIO()

        call_command(
            "import_users",
            "slack",
            "--dry-run",
            "--no-send-welcome",
            stdout=out,
        )

        batch = ImportBatch.objects.get(source=IMPORT_SOURCE_SLACK)
        self.assertTrue(batch.dry_run)
        self.assertEqual(batch.users_created, 1)
        self.assertIn("1 created", out.getvalue())
        self.assertFalse(User.objects.filter(email="dry@example.com").exists())
        self.assertEqual(Schedule.objects.count(), 0)

    @override_settings(SLACK_ENABLED=False, SLACK_BOT_TOKEN="xoxb-test")
    def test_disabled_slack_fails_with_clear_command_error(self):
        clear_config_cache()

        with self.assertRaises(CommandError) as error:
            call_command("import_users", "slack", "--dry-run")

        self.assertIn("SLACK_ENABLED", str(error.exception))

    @override_settings(SLACK_ENABLED=True, SLACK_BOT_TOKEN="")
    def test_missing_token_fails_with_clear_command_error(self):
        clear_config_cache()

        with self.assertRaises(CommandError) as error:
            call_command("import_users", "slack", "--dry-run")

        self.assertIn("SLACK_BOT_TOKEN", str(error.exception))

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_slack_auth_error_fails_import_batch(self, mock_api):
        mock_api.side_effect = SlackAPIError(
            "Slack API error: invalid_auth",
            method="users.list",
            error_code="invalid_auth",
        )

        with self.assertRaises(CommandError) as error:
            call_command("import_users", "slack", "--dry-run")

        self.assertIn("Slack import configuration error", str(error.exception))
        batch = ImportBatch.objects.get(source=IMPORT_SOURCE_SLACK)
        self.assertEqual(batch.status, ImportBatch.STATUS_FAILED)

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_cursor_pagination_imports_members_from_multiple_pages(self, mock_api):
        mock_api.side_effect = [
            users_page([member("U1", "one@example.com")], cursor="NEXT"),
            users_page([member("U2", "two@example.com")]),
        ]

        batch = run_import_batch(
            IMPORT_SOURCE_SLACK,
            slack_workspace_import_adapter,
            send_welcome=False,
        )

        self.assertEqual(batch.users_created, 2)
        self.assertTrue(User.objects.filter(email="one@example.com").exists())
        self.assertTrue(User.objects.filter(email="two@example.com").exists())
        self.assertEqual(mock_api.call_args_list[0].kwargs["limit"], 200)
        self.assertEqual(mock_api.call_args_list[0].kwargs["cursor"], "")
        self.assertEqual(mock_api.call_args_list[1].kwargs["cursor"], "NEXT")

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_filters_non_human_accounts_and_missing_email_is_row_diagnostic(self, mock_api):
        mock_api.return_value = users_page(
            [
                member("USLACKBOT", "bot@example.com", name="slackbot"),
                member("UDEL", "deleted@example.com", deleted=True),
                member("UBOT", "bot@example.com", is_bot=True),
                member("UAPP", "app@example.com", is_app_user=True),
                member("UOWNER", "owner@example.com", is_primary_owner=True),
                member("UMISS", "", real_name="Hidden Email"),
                member("UREAL", "real@example.com"),
            ]
        )

        batch = run_import_batch(
            IMPORT_SOURCE_SLACK,
            slack_workspace_import_adapter,
            send_welcome=False,
        )

        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.users_skipped, 1)
        self.assertEqual(User.objects.count(), 1)
        error = batch.errors[0]
        self.assertEqual(error["kind"], "missing_email")
        self.assertEqual(error["slack_id"], "UMISS")
        self.assertEqual(error["name"], "Hidden Email")
        self.assertIn("visible profile email", error["message"])

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_regular_admin_and_guest_tags_fields_metadata_and_no_tier_grant(self, mock_api):
        mock_api.return_value = users_page(
            [
                member("UREG", "regular@example.com"),
                member("UADMIN", "admin@example.com", is_admin=True),
                member("UGUEST", "guest@example.com", is_ultra_restricted=True),
            ]
        )

        batch = run_import_batch(
            IMPORT_SOURCE_SLACK,
            slack_workspace_import_adapter,
            send_welcome=False,
        )

        self.assertEqual(batch.users_created, 3)
        regular = User.objects.get(email="regular@example.com")
        admin = User.objects.get(email="admin@example.com")
        guest = User.objects.get(email="guest@example.com")
        self.assertEqual(regular.tags, ["slack-member"])
        self.assertEqual(admin.tags, ["slack-member", "slack-admin"])
        self.assertEqual(guest.tags, ["slack-member", "slack-guest"])
        self.assertTrue(regular.slack_member)
        self.assertEqual(regular.slack_user_id, "UREG")
        self.assertIsNotNone(regular.slack_checked_at)
        self.assertEqual(regular.import_source, IMPORT_SOURCE_SLACK)
        self.assertEqual(regular.import_metadata["slack"]["slack_id"], "UREG")
        self.assertEqual(regular.import_metadata["slack"]["slack_team_id"], "T123")
        self.assertNotIn("avatar", regular.import_metadata["slack"])
        self.assertEqual(regular.tier.slug, "free")
        self.assertEqual(TierOverride.objects.count(), 0)

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_existing_user_is_reconciled_without_duplication(self, mock_api):
        existing = User.objects.create_user(
            email="existing@example.com",
            first_name="Existing",
            tags=["before"],
        )
        mock_api.return_value = users_page(
            [member("UEXISTING", "EXISTING@example.com", real_name="Slack Name")]
        )

        batch = run_import_batch(
            IMPORT_SOURCE_SLACK,
            slack_workspace_import_adapter,
            send_welcome=False,
        )

        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(User.objects.filter(email__iexact="existing@example.com").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Existing")
        self.assertEqual(existing.slack_user_id, "UEXISTING")
        self.assertTrue(existing.slack_member)
        self.assertEqual(existing.tags, ["before", "slack-member"])
        self.assertEqual(existing.import_metadata["slack"]["slack_id"], "UEXISTING")

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_existing_slack_id_is_preserved_and_conflict_logged(self, mock_api):
        existing = User.objects.create_user(
            email="conflict@example.com",
            slack_user_id="U_EXISTING",
            import_metadata={"slack": {"slack_id": "U_EXISTING"}},
        )
        mock_api.return_value = users_page([member("U_INCOMING", "conflict@example.com")])

        batch = run_import_batch(
            IMPORT_SOURCE_SLACK,
            slack_workspace_import_adapter,
            send_welcome=False,
        )

        existing.refresh_from_db()
        self.assertEqual(existing.slack_user_id, "U_EXISTING")
        self.assertEqual(User.objects.filter(email="conflict@example.com").count(), 1)
        conflict = [error for error in batch.errors if error.get("kind") == "conflict"][0]
        self.assertEqual(conflict["field"], "slack_user_id")
        self.assertEqual(conflict["incoming_value"], "U_INCOMING")

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_rerun_does_not_duplicate_tags_or_metadata(self, mock_api):
        mock_api.return_value = users_page([member("UREPEAT", "repeat@example.com")])

        run_import_batch(IMPORT_SOURCE_SLACK, slack_workspace_import_adapter, send_welcome=False)
        run_import_batch(IMPORT_SOURCE_SLACK, slack_workspace_import_adapter, send_welcome=False)

        user = User.objects.get(email="repeat@example.com")
        self.assertEqual(user.tags, ["slack-member"])
        self.assertEqual(user.import_metadata["slack"]["slack_id"], "UREPEAT")
        self.assertEqual(User.objects.filter(email="repeat@example.com").count(), 1)


class SlackImportWelcomeEmailTest(TestCase):
    @patch("email_app.services.email_service.EmailService._send_ses", return_value="ses-1")
    def test_slack_welcome_copy_explains_workspace_context(self, mock_send):
        user = User.objects.create_user(
            email="welcome-slack@example.com",
            import_source=IMPORT_SOURCE_SLACK,
            import_metadata={"slack": {"slack_id": "UWELCOME"}},
            tags=["slack-member"],
        )

        result = send_imported_welcome_email(user.pk)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(
            EmailLog.objects.filter(user=user, email_type="welcome_imported").count(),
            1,
        )
        html_body = mock_send.call_args.args[2]
        self.assertIn("AI Shipping Labs Slack workspace", html_body)
        self.assertIn("Free account", html_body)
        self.assertIn("does not grant paid membership", html_body)
        self.assertIn("Set your password", html_body)
        self.assertIn("Sign in to AI Shipping Labs", html_body)
        self.assertIn("/api/unsubscribe?token=", html_body)
        self.assertIn("account deletion", html_body)
