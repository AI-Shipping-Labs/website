"""Backfill missed bounce side-effects for users (issue #765).

One-shot recovery tool. The SES webhook at ``POST /api/ses-events`` was
not being called for a window before the infra-side HTTPS SNS
subscription landed, so a backlog of permanent bounces never marked the
matching User rows as unsubscribed. This command applies the same
side-effects that ``api.views.ses_events._handle_bounce`` would have
applied -- without forging a SES payload.

Invocation modes::

    python manage.py process_missed_bounces --email taylordisom@gmaill.com
    python manage.py process_missed_bounces --email a@x.com --email b@y.com
    python manage.py process_missed_bounces --since 2026-05-01
    python manage.py process_missed_bounces --since 2026-05-01 --until 2026-05-21
    python manage.py process_missed_bounces --emails-from /path/to/file.txt
    python manage.py process_missed_bounces --since 2026-05-01 --dry-run

Idempotent: re-running with the same input is a no-op. A user that
already has a matching ``SesEvent`` row of type
``bounce_permanent`` / ``bounce_transient`` / ``bounce_other`` is
skipped.

Per-marked user the command writes a synthetic ``SesEvent`` row so the
audit trail explains why those rows exist::

    message_id = "backfill-<uuid>"
    event_type = bounce_permanent
    bounce_type = Permanent
    diagnostic_code = "backfilled via process_missed_bounces"
    raw_payload = {"backfill": True, "source": "...", "ran_at": "..."}

The command branches on ``hasattr(user, "bounce_state")`` so it works
both before and after issue #766 lands. When the structured
``bounce_state`` field is available, it is written along with
``bounce_recorded_at`` and ``last_bounce_diagnostic``. Otherwise the
legacy ``bounced`` tag path is used (matches
``api.views.ses_events._mark_permanent_bounce``).
"""

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.utils.tags import add_tag
from email_app.models import SesEvent

User = get_user_model()

TAG_BOUNCED = "bounced"
BACKFILL_DIAGNOSTIC = "backfilled via process_missed_bounces"
BACKFILL_SOURCE = "process_missed_bounces"


class Command(BaseCommand):
    help = (
        "Backfill permanent-bounce side-effects (unsubscribed + tag / "
        "bounce_state) for users whose bounce was missed by the SES "
        "webhook during the dead-webhook window."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            action="append",
            default=[],
            metavar="ADDR",
            help=(
                "Single address to mark as permanently bounced. May be "
                "passed multiple times for a small batch."
            ),
        )
        parser.add_argument(
            "--emails-from",
            metavar="PATH",
            help=(
                "Path to a file with one email address per line. Blank "
                "lines and lines starting with # are skipped."
            ),
        )
        parser.add_argument(
            "--since",
            metavar="YYYY-MM-DD",
            help=(
                "Mark all Users whose date_joined is on or after this date "
                "and who do not already have a bounce SesEvent row. "
                "Treat this as 'users who signed up in the dead-webhook "
                "window and might have bounced'."
            ),
        )
        parser.add_argument(
            "--until",
            metavar="YYYY-MM-DD",
            help=(
                "Optional upper bound on --since (date_joined strictly "
                "less than midnight of the day AFTER this date). Default: "
                "no upper bound."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Print planned actions but do not save -- no User mutations "
                "and no SesEvent rows written."
            ),
        )

    def handle(self, *args, **options):
        emails_arg = list(options.get("email") or [])
        emails_from = options.get("emails_from")
        since_raw = options.get("since")
        until_raw = options.get("until")
        dry_run = bool(options.get("dry_run"))

        if not emails_arg and not emails_from and not since_raw:
            raise CommandError(
                "Specify at least one of --email, --emails-from, or --since. "
                "Run with --help for usage."
            )

        # Collect addresses from --email and --emails-from (preserves order,
        # deduped case-insensitively).
        addresses = list(emails_arg)
        if emails_from:
            addresses.extend(_read_addresses_from_file(emails_from))

        if since_raw:
            since_date = _parse_date_or_error(since_raw, "--since")
            until_date = None
            if until_raw:
                until_date = _parse_date_or_error(until_raw, "--until")
            addresses.extend(
                self._addresses_from_since(since_date, until_date)
            )

        # Dedupe case-insensitively while preserving first-seen ordering.
        seen = set()
        ordered = []
        for raw in addresses:
            normalized = (raw or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(normalized)

        processed = 0
        marked = 0
        skipped_no_user = 0
        skipped_existing_event = 0

        for address in ordered:
            processed += 1
            outcome = self._process_address(address, dry_run=dry_run)
            if outcome == "marked":
                marked += 1
            elif outcome == "no_user":
                skipped_no_user += 1
            elif outcome == "existing_event":
                skipped_existing_event += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed}; marked={marked}; "
                f"skipped_no_user={skipped_no_user}; "
                f"skipped_existing_event={skipped_existing_event}; "
                f"dry_run={dry_run}"
            )
        )

    # ------------------------------------------------------------------
    # Per-address pipeline
    # ------------------------------------------------------------------

    def _process_address(self, address, *, dry_run):
        """Mark one address as permanently bounced.

        Returns one of:
            - ``"marked"`` -- new SesEvent row written and user mutated
              (or would have been, under --dry-run).
            - ``"no_user"`` -- no matching User row, nothing to do.
            - ``"existing_event"`` -- user already has a bounce SesEvent,
              skipped for idempotency.
        """
        user = User.objects.filter(email__iexact=address).first()
        if user is None:
            self.stdout.write(f"{address}: no matching user -- skipped")
            return "no_user"

        already_processed = SesEvent.objects.filter(
            recipient_email__iexact=address,
            event_type__startswith="bounce_",
        ).exists()
        if already_processed:
            self.stdout.write(
                f"{address}: already processed (SesEvent exists) -- skipped"
            )
            return "existing_event"

        if dry_run:
            self.stdout.write(
                f"{address}: would mark permanent bounce "
                f"(user_id={user.id})"
            )
            return "marked"

        action_taken = _mark_permanent_bounce(user)

        SesEvent.objects.create(
            message_id=f"backfill-{uuid.uuid4()}",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            recipient_email=address,
            user=user,
            bounce_type="Permanent",
            bounce_subtype="",
            diagnostic_code=BACKFILL_DIAGNOSTIC,
            action_taken=action_taken[:255],
            raw_payload={
                "backfill": True,
                "source": BACKFILL_SOURCE,
                "ran_at": timezone.now().isoformat(),
            },
        )
        self.stdout.write(
            f"{address}: marked permanent bounce (user_id={user.id})"
        )
        return "marked"

    # ------------------------------------------------------------------
    # --since selector
    # ------------------------------------------------------------------

    def _addresses_from_since(self, since_date, until_date):
        """Return emails of users in the date range without bounce events.

        ``since_date`` is inclusive (date_joined >= 00:00 on that date).
        ``until_date`` is inclusive on the day boundary (date_joined <
        00:00 on the day AFTER until_date). Both are interpreted in the
        current timezone.
        """
        from datetime import datetime, time

        tz = timezone.get_current_timezone()
        since_dt = timezone.make_aware(
            datetime.combine(since_date, time.min), tz,
        )
        queryset = User.objects.filter(date_joined__gte=since_dt)
        if until_date is not None:
            # Inclusive upper bound: include all rows whose date_joined
            # falls on or before the until_date. Add one day to push the
            # comparison boundary past 23:59:59 of until_date.
            from datetime import timedelta
            until_dt = timezone.make_aware(
                datetime.combine(until_date + timedelta(days=1), time.min),
                tz,
            )
            queryset = queryset.filter(date_joined__lt=until_dt)

        # Skip users that already have a bounce SesEvent row.
        already_bounced = set(
            SesEvent.objects.filter(event_type__startswith="bounce_")
            .exclude(recipient_email="")
            .values_list("recipient_email", flat=True)
        )
        already_bounced_lower = {a.lower() for a in already_bounced}

        addresses = []
        for email in queryset.order_by("date_joined").values_list(
            "email", flat=True,
        ):
            if not email:
                continue
            if email.lower() in already_bounced_lower:
                continue
            addresses.append(email)
        return addresses


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date_or_error(raw, flag):
    parsed = parse_date(raw)
    if parsed is None:
        raise CommandError(
            f"{flag}: could not parse {raw!r} as a YYYY-MM-DD date."
        )
    return parsed


def _read_addresses_from_file(path):
    """Yield one address per non-blank, non-comment line in ``path``."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        raise CommandError(
            f"--emails-from: could not read {path!r}: {exc}"
        ) from exc

    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _mark_permanent_bounce(user):
    """Apply the permanent-bounce side-effects to ``user``.

    Returns the ``action_taken`` string for the synthetic SesEvent row.
    Branches on ``hasattr(user, "bounce_state")`` so the command works
    whether issue #766 has landed yet or not.
    """
    if hasattr(user, "bounce_state"):
        # #766-world: structured bounce_state field replaces the tag.
        bounce_state_enum = getattr(type(user), "BounceState", None)
        permanent_value = (
            getattr(bounce_state_enum, "PERMANENT", "permanent")
            if bounce_state_enum is not None
            else "permanent"
        )
        user.bounce_state = permanent_value
        user.bounce_recorded_at = timezone.now()
        user.last_bounce_diagnostic = BACKFILL_DIAGNOSTIC
        user.unsubscribed = True
        user.save(
            update_fields=[
                "bounce_state",
                "bounce_recorded_at",
                "last_bounce_diagnostic",
                "unsubscribed",
            ]
        )
        return "unsubscribed and set bounce_state=PERMANENT (backfill)"

    # Pre-#766 world: legacy tag-based bookkeeping. Mirrors
    # ``api.views.ses_events._mark_permanent_bounce``.
    if not user.unsubscribed:
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
    add_tag(user, TAG_BOUNCED)
    return f"unsubscribed and tagged {TAG_BOUNCED} (backfill)"
