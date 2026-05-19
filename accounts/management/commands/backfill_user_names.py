"""Backfill ``User.first_name`` / ``User.last_name`` for existing users
with blank names (issue #710).

One-shot management command — not a periodic task. #699 added
forward-looking name capture on every registration path, so this
command is only needed to clean up the existing backlog. Re-running is
a no-op (the do-not-overwrite invariant in
:func:`accounts.utils.names.set_name_from_external` makes this
automatic).

Per-user logic:

1. Skip if either name field is already populated (early exit avoids
   needless API calls; ``set_name_from_external`` would also skip).
2. If ``stripe_customer_id`` is set and Stripe is configured:
   ``stripe.Customer.retrieve`` and try ``customer.name``.
3. If names are still blank and ``slack_user_id`` is set: probe
   ``SlackCommunityService.lookup_user_profile_by_email`` and fall
   back to ``real_name`` when the profile's split fields are empty.
4. Save with ``update_fields=['first_name', 'last_name']``.

Sister command: ``payments/management/commands/backfill_stripe_tiers.py``
— same flag handling, same per-user output, same summary line shape.
"""

import time

import stripe
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.utils.names import set_name_from_external
from community.services.slack import SlackAPIError, SlackCommunityService
from integrations.config import get_config

User = get_user_model()

# Rate-limit pacing between API calls.
#
# Stripe paid-tier read calls allow ~100 RPS; 0.05s (~20 RPS) keeps us
# well under any plausible cap with zero coordination logic.
STRIPE_SLEEP_SECONDS = 0.05

# Slack ``users.lookupByEmail`` is Tier 4 (50 RPM). 0.7s gives ~85 RPM
# which is over the documented limit but Slack publishes burst headroom
# above the steady-state rate; combined with the small one-shot backlog
# we expect, this is safe. Drop to 1.5s if Slack starts returning
# ``ratelimited``.
SLACK_SLEEP_SECONDS = 0.7


class Command(BaseCommand):
    help = (
        "Backfill first_name / last_name from Stripe and Slack for "
        "users whose name fields are both blank."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Report proposed changes per user and a summary, but do "
                "not call user.save() or mutate the DB."
            ),
        )
        parser.add_argument(
            "--email",
            help=(
                "Restrict processing to a single user by email — useful "
                "for triaging individual reports."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        email = options.get("email")

        queryset = User.objects.filter(
            first_name="", last_name="",
        ).order_by("email")

        if email:
            queryset = queryset.filter(email__iexact=email)
            if not queryset.exists():
                raise CommandError(
                    f"No user with blank first/last name found for {email}."
                )

        stripe_secret = get_config("STRIPE_SECRET_KEY", "")
        slack_service = SlackCommunityService()

        processed = 0
        changed = 0
        dry_runs = 0
        skipped = 0
        warnings = 0

        for user in queryset:
            processed += 1
            outcome = self._process_user(
                user,
                stripe_secret=stripe_secret,
                slack_service=slack_service,
                dry_run=dry_run,
            )
            if outcome == "changed":
                changed += 1
            elif outcome == "dry_run":
                dry_runs += 1
            elif outcome == "warning":
                warnings += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Processed "
                f"{processed}; changed={changed}; dry_run={dry_runs}; "
                f"skipped={skipped}; warnings={warnings}"
            )
        )

    # ------------------------------------------------------------------
    # Per-user pipeline.
    # ------------------------------------------------------------------

    def _process_user(self, user, *, stripe_secret, slack_service, dry_run):
        """Run Stripe then Slack for one user.

        Returns one of:
            - ``"changed"`` — names mutated and saved.
            - ``"dry_run"`` — names would have been mutated, no save.
            - ``"warning"`` — at least one API call failed; user skipped.
            - ``"skipped"`` — no source available; nothing to do.
        """
        any_warning = False
        any_source_attempted = False

        # ----- Stripe path -----
        if user.stripe_customer_id and stripe_secret:
            any_source_attempted = True
            try:
                customer = stripe.Customer.retrieve(
                    user.stripe_customer_id, api_key=stripe_secret,
                )
            except stripe.InvalidRequestError as exc:
                self.stderr.write(
                    f"{user.email}: warning: Stripe lookup failed: "
                    f"{_first_line(exc)}"
                )
                any_warning = True
            else:
                full_name = _stripe_customer_name(customer)
                if full_name:
                    set_name_from_external(
                        user, full_name=full_name, source="stripe",
                    )
            # Pace Stripe calls regardless of outcome.
            time.sleep(STRIPE_SLEEP_SECONDS)
        elif user.stripe_customer_id and not stripe_secret:
            # Stripe customer ID present but credentials missing: warn
            # once per user so operators see the credential gap, and
            # fall through to Slack.
            self.stderr.write(
                f"{user.email}: warning: Stripe not configured "
                f"(STRIPE_SECRET_KEY unset); skipping Stripe lookup"
            )
            any_warning = True

        # ----- Slack path (only if names are still blank) -----
        if not (user.first_name or user.last_name) and user.slack_user_id:
            any_source_attempted = True
            slack_profile = None
            slack_failed = False
            try:
                slack_profile = slack_service.lookup_user_profile_by_email(
                    user.email,
                )
            except SlackAPIError as exc:
                self.stderr.write(
                    f"{user.email}: warning: Slack lookup failed: "
                    f"{_first_line(exc)}"
                )
                any_warning = True
                slack_failed = True

            if slack_profile is None and not slack_failed:
                self.stderr.write(
                    f"{user.email}: warning: Slack profile not found "
                    f"(user not in workspace)"
                )
                any_warning = True
            elif slack_profile is not None:
                first = (slack_profile.get("first_name") or "").strip()
                last = (slack_profile.get("last_name") or "").strip()
                if first or last:
                    set_name_from_external(
                        user, first=first, last=last, source="slack_probe",
                    )
                else:
                    real_name = (slack_profile.get("real_name") or "").strip()
                    if real_name:
                        set_name_from_external(
                            user, full_name=real_name, source="slack_probe",
                        )

            time.sleep(SLACK_SLEEP_SECONDS)

        # ----- Resolve outcome -----
        mutated = bool(user.first_name or user.last_name)

        if mutated and dry_run:
            self.stdout.write(
                f"{user.email}: would set "
                f"first_name={user.first_name!r} last_name={user.last_name!r}"
            )
            return "dry_run"

        if mutated:
            user.save(update_fields=["first_name", "last_name"])
            self.stdout.write(
                f"{user.email}: set "
                f"first_name={user.first_name!r} last_name={user.last_name!r}"
            )
            return "changed"

        if any_warning:
            return "warning"

        if not any_source_attempted:
            # No Stripe customer ID and no Slack user ID — nothing to do.
            self.stdout.write(
                f"{user.email}: skipped (no Stripe or Slack identity)"
            )
        else:
            # Tried at least one source but found no usable name.
            self.stdout.write(
                f"{user.email}: skipped (no name available from sources)"
            )
        return "skipped"


def _stripe_customer_name(customer):
    """Return ``customer.name`` from a Stripe Customer payload, or ``""``.

    Tolerates both ``StripeObject`` (attribute access) and plain dict
    payloads (mock-friendly).
    """
    if customer is None:
        return ""
    if isinstance(customer, dict):
        return (customer.get("name") or "").strip()
    name = getattr(customer, "name", "") or ""
    return name.strip()


def _first_line(exc):
    """First line of an exception's message, with a sensible fallback."""
    message = str(exc).splitlines()[0] if str(exc) else ""
    return message or exc.__class__.__name__
