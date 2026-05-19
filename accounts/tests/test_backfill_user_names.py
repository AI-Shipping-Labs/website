"""Tests for ``backfill_user_names`` management command (issue #710)."""

import logging
from io import StringIO
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from community.services.slack import SlackAPIError

User = get_user_model()


def _stripe_customer(name=""):
    """Build a fake Stripe customer payload (dict-shaped)."""
    return {"id": "cus_test", "name": name}


@override_settings(STRIPE_SECRET_KEY="sk_test_backfill_names")
class BackfillUserNamesCommandTest(TestCase):
    """Behaviour of the ``backfill_user_names`` command."""

    # ------------------------------------------------------------------
    # Stripe-only path.
    # ------------------------------------------------------------------

    def test_stripe_only_path_saves_name(self):
        user = User.objects.create_user(
            email="stripe-only@test.com",
            password="x",
            stripe_customer_id="cus_stripe_only",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ) as stripe_call:
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        stripe_call.assert_called_once()
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_stripe_only_emits_info_log_from_helper(self):
        user = User.objects.create_user(
            email="logged@test.com",
            password="x",
            stripe_customer_id="cus_logged",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ):
            with self.assertLogs("accounts.utils.names", level="INFO") as cm:
                call_command(
                    "backfill_user_names",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        # Helper emits ``set_name_from_external: user=<pk> source=stripe fields=...``
        # with no PII in the body.
        joined = "\n".join(cm.output)
        self.assertIn("set_name_from_external", joined)
        self.assertIn(f"user={user.pk}", joined)
        self.assertIn("source=stripe", joined)
        self.assertIn("first_name", joined)
        self.assertIn("last_name", joined)

    def test_summary_line_uses_sister_command_shape(self):
        User.objects.create_user(
            email="summary@test.com",
            password="x",
            stripe_customer_id="cus_summary",
        )
        out = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ):
            call_command(
                "backfill_user_names",
                stdout=out,
                stderr=StringIO(),
            )

        text = out.getvalue()
        self.assertIn(
            "Processed 1; changed=1; dry_run=0; skipped=0; warnings=0",
            text,
        )

    # ------------------------------------------------------------------
    # Slack-only path.
    # ------------------------------------------------------------------

    def test_slack_only_path_saves_name_from_split_fields(self):
        user = User.objects.create_user(
            email="slack-only@test.com",
            password="x",
            slack_user_id="U_SLACK",
        )

        slack_profile = {
            "id": "U_SLACK",
            "real_name": "Alex Grigorev",
            "first_name": "Alex",
            "last_name": "Grigorev",
        }
        with patch(
            "accounts.management.commands.backfill_user_names."
            "SlackCommunityService.lookup_user_profile_by_email",
            return_value=slack_profile,
        ) as slack_call:
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        slack_call.assert_called_once_with("slack-only@test.com")
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_slack_real_name_fallback_when_split_fields_blank(self):
        user = User.objects.create_user(
            email="realname@test.com",
            password="x",
            slack_user_id="U_REAL",
        )

        slack_profile = {
            "id": "U_REAL",
            "real_name": "Salvador Castillo Raya",
            "first_name": "",
            "last_name": "",
        }
        with patch(
            "accounts.management.commands.backfill_user_names."
            "SlackCommunityService.lookup_user_profile_by_email",
            return_value=slack_profile,
        ):
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Salvador Castillo")
        self.assertEqual(user.last_name, "Raya")

    # ------------------------------------------------------------------
    # Stripe-preferred ordering when both identities exist.
    # ------------------------------------------------------------------

    def test_stripe_preferred_over_slack_when_both_available(self):
        user = User.objects.create_user(
            email="both@test.com",
            password="x",
            stripe_customer_id="cus_both",
            slack_user_id="U_BOTH",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ):
            with patch(
                "accounts.management.commands.backfill_user_names."
                "SlackCommunityService.lookup_user_profile_by_email",
            ) as slack_call:
                call_command(
                    "backfill_user_names",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        # Slack must not be called when Stripe already provided a name.
        slack_call.assert_not_called()
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")

    def test_slack_called_when_stripe_returns_blank_name(self):
        user = User.objects.create_user(
            email="stripe-blank@test.com",
            password="x",
            stripe_customer_id="cus_blank",
            slack_user_id="U_FALLBACK",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer(""),
        ):
            with patch(
                "accounts.management.commands.backfill_user_names."
                "SlackCommunityService.lookup_user_profile_by_email",
                return_value={
                    "id": "U_FALLBACK",
                    "real_name": "",
                    "first_name": "Slack",
                    "last_name": "User",
                },
            ) as slack_call:
                call_command(
                    "backfill_user_names",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        slack_call.assert_called_once()
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Slack")
        self.assertEqual(user.last_name, "User")

    # ------------------------------------------------------------------
    # Dry-run.
    # ------------------------------------------------------------------

    def test_dry_run_prints_proposal_and_does_not_save(self):
        user = User.objects.create_user(
            email="dryrun@test.com",
            password="x",
            stripe_customer_id="cus_dryrun",
        )
        out = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ):
            call_command(
                "backfill_user_names",
                "--dry-run",
                stdout=out,
                stderr=StringIO(),
            )

        user.refresh_from_db()
        # In-memory mutations must not have been persisted.
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")

        text = out.getvalue()
        self.assertIn("dryrun@test.com: would set", text)
        self.assertIn(
            "Processed 1; changed=0; dry_run=1; skipped=0; warnings=0",
            text,
        )

    def test_dry_run_makes_no_db_writes(self):
        """Hard assertion: zero User row changes under --dry-run."""
        User.objects.create_user(
            email="nowrite@test.com",
            password="x",
            stripe_customer_id="cus_nowrite",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ):
            with patch.object(User, "save") as save_call:
                call_command(
                    "backfill_user_names",
                    "--dry-run",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        save_call.assert_not_called()

    # ------------------------------------------------------------------
    # Idempotency.
    # ------------------------------------------------------------------

    def test_second_invocation_is_a_noop(self):
        User.objects.create_user(
            email="idempotent@test.com",
            password="x",
            stripe_customer_id="cus_idempotent",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ) as stripe_call:
            # First run: writes the name.
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )
            first_call_count = stripe_call.call_count

            # Second run: queryset now excludes this user (first/last
            # are no longer blank), so Stripe is never called again
            # and nothing changes.
            out2 = StringIO()
            with patch.object(User, "save") as save_call:
                call_command(
                    "backfill_user_names",
                    stdout=out2,
                    stderr=StringIO(),
                )

        save_call.assert_not_called()
        # The Stripe API was not called a second time — queryset filter
        # eliminated the now-populated user.
        self.assertEqual(stripe_call.call_count, first_call_count)
        self.assertIn(
            "Processed 0; changed=0; dry_run=0; skipped=0; warnings=0",
            out2.getvalue(),
        )

    # ------------------------------------------------------------------
    # Error handling.
    # ------------------------------------------------------------------

    def test_stripe_deleted_customer_invalid_request_error_is_warning(self):
        user = User.objects.create_user(
            email="deleted@test.com",
            password="x",
            stripe_customer_id="cus_deleted",
        )
        err = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            side_effect=stripe.InvalidRequestError(
                "No such customer: 'cus_deleted'", param="id",
            ),
        ):
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=err,
            )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")
        self.assertIn("Stripe lookup failed", err.getvalue())

    def test_stripe_error_does_not_crash_run_for_subsequent_users(self):
        bad = User.objects.create_user(
            email="aaa-bad@test.com",
            password="x",
            stripe_customer_id="cus_bad",
        )
        good = User.objects.create_user(
            email="zzz-good@test.com",
            password="x",
            stripe_customer_id="cus_good",
        )

        def retrieve(customer_id, **kwargs):
            if customer_id == "cus_bad":
                raise stripe.InvalidRequestError(
                    "No such customer", param="id",
                )
            return _stripe_customer("Good Name")

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            side_effect=retrieve,
        ):
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertEqual(bad.first_name, "")
        self.assertEqual(good.first_name, "Good")
        self.assertEqual(good.last_name, "Name")

    def test_slack_api_error_is_warning(self):
        user = User.objects.create_user(
            email="slack-error@test.com",
            password="x",
            slack_user_id="U_ERR",
        )
        err = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "SlackCommunityService.lookup_user_profile_by_email",
            side_effect=SlackAPIError(
                "Slack API error: ratelimited",
                method="users.lookupByEmail",
                error_code="ratelimited",
            ),
        ):
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=err,
            )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertIn("Slack lookup failed", err.getvalue())

    def test_slack_none_return_is_warning(self):
        user = User.objects.create_user(
            email="slack-none@test.com",
            password="x",
            slack_user_id="U_NONE",
        )
        err = StringIO()
        out = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "SlackCommunityService.lookup_user_profile_by_email",
            return_value=None,
        ):
            call_command(
                "backfill_user_names",
                stdout=out,
                stderr=err,
            )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertIn("Slack profile not found", err.getvalue())
        self.assertIn("warnings=1", out.getvalue())

    # ------------------------------------------------------------------
    # --email flag.
    # ------------------------------------------------------------------

    def test_email_filter_processes_only_named_user(self):
        target = User.objects.create_user(
            email="target@test.com",
            password="x",
            stripe_customer_id="cus_target",
        )
        other = User.objects.create_user(
            email="other@test.com",
            password="x",
            stripe_customer_id="cus_other",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ) as stripe_call:
            call_command(
                "backfill_user_names",
                "--email",
                "target@test.com",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        # Stripe called exactly once (for the target user).
        stripe_call.assert_called_once()
        target.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(target.first_name, "Alex")
        self.assertEqual(other.first_name, "")

    def test_email_filter_no_match_raises_command_error(self):
        User.objects.create_user(
            email="exists@test.com",
            password="x",
        )
        with self.assertRaises(CommandError):
            call_command(
                "backfill_user_names",
                "--email",
                "missing@test.com",
                stdout=StringIO(),
                stderr=StringIO(),
            )

    # ------------------------------------------------------------------
    # Skip path for users without any identity source.
    # ------------------------------------------------------------------

    def test_user_without_stripe_or_slack_is_skipped_without_api_calls(self):
        user = User.objects.create_user(
            email="orphan@test.com",
            password="x",
        )
        out = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
        ) as stripe_call:
            with patch(
                "accounts.management.commands.backfill_user_names."
                "SlackCommunityService.lookup_user_profile_by_email",
            ) as slack_call:
                call_command(
                    "backfill_user_names",
                    stdout=out,
                    stderr=StringIO(),
                )

        stripe_call.assert_not_called()
        slack_call.assert_not_called()
        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertIn("Processed 1; changed=0", out.getvalue())
        self.assertIn("skipped=1", out.getvalue())

    # ------------------------------------------------------------------
    # Already-populated users.
    # ------------------------------------------------------------------

    def test_already_populated_users_are_not_processed(self):
        """Queryset filter excludes users whose names are already set."""
        already = User.objects.create_user(
            email="already@test.com",
            password="x",
            first_name="Existing",
            last_name="User",
            stripe_customer_id="cus_already",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
        ) as stripe_call:
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        # Stripe must not be called for already-populated users.
        stripe_call.assert_not_called()
        already.refresh_from_db()
        self.assertEqual(already.first_name, "Existing")
        self.assertEqual(already.last_name, "User")

    def test_user_with_only_first_name_set_is_not_processed(self):
        """Filter requires BOTH fields blank — partial fills are out of scope."""
        partial = User.objects.create_user(
            email="partial@test.com",
            password="x",
            first_name="Only",
            stripe_customer_id="cus_partial",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
        ) as stripe_call:
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        stripe_call.assert_not_called()
        partial.refresh_from_db()
        self.assertEqual(partial.first_name, "Only")
        self.assertEqual(partial.last_name, "")


@override_settings(STRIPE_SECRET_KEY="")
class BackfillUserNamesNoCredentialsTest(TestCase):
    """Behaviour when Stripe credentials are missing.

    No-credentials policy: per-user warning, fall through to Slack;
    users with no usable source are skipped (not a hard CommandError).
    """

    def test_no_stripe_key_skips_stripe_with_warning_falls_through_to_slack(self):
        user = User.objects.create_user(
            email="no-stripe-key@test.com",
            password="x",
            stripe_customer_id="cus_no_key",
            slack_user_id="U_FALLBACK",
        )
        err = StringIO()

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
        ) as stripe_call:
            with patch(
                "accounts.management.commands.backfill_user_names."
                "SlackCommunityService.lookup_user_profile_by_email",
                return_value={
                    "id": "U_FALLBACK",
                    "real_name": "",
                    "first_name": "Slack",
                    "last_name": "Fallback",
                },
            ) as slack_call:
                call_command(
                    "backfill_user_names",
                    stdout=StringIO(),
                    stderr=err,
                )

        # Stripe lookup never invoked (no credentials).
        stripe_call.assert_not_called()
        # Slack lookup was used as fallback.
        slack_call.assert_called_once()
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Slack")
        self.assertEqual(user.last_name, "Fallback")
        self.assertIn("Stripe not configured", err.getvalue())

    def test_no_stripe_key_and_no_slack_user_id_skips_user(self):
        """Command still completes when no source is usable."""
        user = User.objects.create_user(
            email="no-sources@test.com",
            password="x",
            stripe_customer_id="cus_nosrc",
        )
        out = StringIO()
        err = StringIO()

        call_command(
            "backfill_user_names",
            stdout=out,
            stderr=err,
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        # Per-user warning emitted; user counted as ``warnings``.
        self.assertIn("Stripe not configured", err.getvalue())
        self.assertIn("Processed 1", out.getvalue())
        self.assertIn("warnings=1", out.getvalue())


class BackfillUserNamesQuerysetOrderingTest(TestCase):
    """Queryset is ordered by email — deterministic across runs."""

    @override_settings(STRIPE_SECRET_KEY="sk_test")
    def test_processes_users_in_email_order(self):
        User.objects.create_user(
            email="charlie@test.com",
            password="x",
            stripe_customer_id="cus_c",
        )
        User.objects.create_user(
            email="alpha@test.com",
            password="x",
            stripe_customer_id="cus_a",
        )
        User.objects.create_user(
            email="bravo@test.com",
            password="x",
            stripe_customer_id="cus_b",
        )

        seen = []

        def retrieve(customer_id, **kwargs):
            seen.append(customer_id)
            return _stripe_customer("X Y")

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            side_effect=retrieve,
        ):
            call_command(
                "backfill_user_names",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        self.assertEqual(seen, ["cus_a", "cus_b", "cus_c"])


@override_settings(STRIPE_SECRET_KEY="sk_test_rate")
class BackfillUserNamesRateLimitTest(TestCase):
    """Rate-limit pacing: time.sleep is invoked between API calls."""

    def test_stripe_path_sleeps_for_rate_limit_pacing(self):
        User.objects.create_user(
            email="paced@test.com",
            password="x",
            stripe_customer_id="cus_paced",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "stripe.Customer.retrieve",
            return_value=_stripe_customer("Alex Grigorev"),
        ):
            with patch(
                "accounts.management.commands.backfill_user_names.time.sleep"
            ) as sleep_call:
                call_command(
                    "backfill_user_names",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        # At least one sleep happened with the Stripe budget.
        sleep_args = [call.args[0] for call in sleep_call.call_args_list]
        self.assertIn(0.05, sleep_args)

    def test_slack_path_sleeps_for_rate_limit_pacing(self):
        User.objects.create_user(
            email="paced-slack@test.com",
            password="x",
            slack_user_id="U_PACED",
        )

        with patch(
            "accounts.management.commands.backfill_user_names."
            "SlackCommunityService.lookup_user_profile_by_email",
            return_value={
                "id": "U_PACED",
                "real_name": "Alex Grigorev",
                "first_name": "Alex",
                "last_name": "Grigorev",
            },
        ):
            with patch(
                "accounts.management.commands.backfill_user_names.time.sleep"
            ) as sleep_call:
                call_command(
                    "backfill_user_names",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

        sleep_args = [call.args[0] for call in sleep_call.call_args_list]
        self.assertIn(0.7, sleep_args)


# Silence helper INFO log noise except where we explicitly capture it.
logging.getLogger("accounts.utils.names").setLevel(logging.WARNING)
