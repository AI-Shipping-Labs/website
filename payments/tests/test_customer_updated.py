"""Tests for the ``customer.updated`` webhook handler (#643).

The Stripe Customer Portal lets users edit their billing email. When a
user does that, Stripe fires ``customer.updated`` carrying the new
email in ``data.object.email``. This module's tests pin down the
behavior of ``handle_customer_updated``:

- It syncs ONLY the email field. ``customer.updated`` can also carry
  ``name``, ``metadata``, ``phone``, etc., and the handler must ignore
  all of those.
- It is a no-op when the email is empty, missing, or already matches.
- It raises ``WebhookPermanentError`` on a unique-collision so the
  dispatcher records a terminal ``failed_permanent`` row instead of
  letting Stripe keep retrying.
- The handler is wired into the webhook dispatcher's ``EVENT_HANDLERS``
  map so the live endpoint actually invokes it.
"""

import json

from django.test import TestCase, tag

from accounts.models import User
from community.models import CommunityAuditLog
from payments.exceptions import WebhookPermanentError
from payments.services import handle_customer_updated
from payments.views.webhooks import EVENT_HANDLERS


def _make_customer_payload(
    customer_id="cus_test_643",
    email="new@example.com",
    **extra,
):
    """Build a minimal ``customer.updated`` data.object payload.

    Mirrors the shape Stripe sends: ``id`` is the customer id, ``email``
    is the new billing email. Extra keys are merged in so individual
    tests can simulate ``name``/``metadata``/``phone`` payloads when
    they need to assert those fields are ignored.
    """
    payload = {"id": customer_id, "email": email}
    payload.update(extra)
    return payload


@tag("core")
class CustomerUpdatedHandlerTest(TestCase):
    """Tests for ``handle_customer_updated`` (Stripe email sync)."""

    def test_customer_updated_syncs_changed_email(self):
        """A new, unique email replaces the local user's email."""
        user = User.objects.create_user(email="old@example.com")
        user.stripe_customer_id = "cus_sync_1"
        user.save(update_fields=["stripe_customer_id"])

        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_sync_1",
                email="new@example.com",
            )
        )

        user.refresh_from_db()
        self.assertEqual(user.email, "new@example.com")

    def test_customer_updated_with_matching_email_is_noop(self):
        """If Stripe sends the same email, nothing changes, no audit row."""
        user = User.objects.create_user(email="same@example.com")
        user.stripe_customer_id = "cus_match_1"
        user.save(update_fields=["stripe_customer_id"])
        audit_count_before = CommunityAuditLog.objects.count()

        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_match_1",
                # Same email with a different casing — Stripe sometimes
                # normalizes casing on its side; we still treat this as
                # idempotent because emails are case-insensitive.
                email="Same@Example.com",
            )
        )

        user.refresh_from_db()
        self.assertEqual(user.email, "same@example.com")
        self.assertEqual(
            CommunityAuditLog.objects.count(),
            audit_count_before,
            "Matching-email event must not write an audit log row.",
        )

    def test_customer_updated_with_unknown_customer_id_is_noop(self):
        """Unknown ``stripe_customer_id`` returns cleanly with no side effects."""
        # No user has stripe_customer_id="cus_ghost_643". The handler
        # must NOT raise and must NOT create an audit log row. We log
        # at INFO and return — the dispatcher then skips recording a
        # WebhookEvent row, matching handle_subscription_updated.
        audit_count_before = CommunityAuditLog.objects.count()
        user_count_before = User.objects.count()

        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_ghost_643",
                email="ghost@example.com",
            )
        )

        self.assertEqual(
            CommunityAuditLog.objects.count(),
            audit_count_before,
            "Unknown customer must not write an audit log row.",
        )
        self.assertEqual(
            User.objects.count(),
            user_count_before,
            "Unknown customer must not create a new user.",
        )

    def test_customer_updated_with_empty_email_is_noop(self):
        """Empty/missing email leaves the local email untouched."""
        user = User.objects.create_user(email="kept@example.com")
        user.stripe_customer_id = "cus_empty_1"
        user.save(update_fields=["stripe_customer_id"])
        audit_count_before = CommunityAuditLog.objects.count()

        # Stripe sends the full customer object on every update event —
        # if only the name changed, the email field may be empty or
        # missing entirely. Both shapes must be treated as no-op.
        handle_customer_updated(
            _make_customer_payload(customer_id="cus_empty_1", email="")
        )
        handle_customer_updated({"id": "cus_empty_1"})

        user.refresh_from_db()
        self.assertEqual(user.email, "kept@example.com")
        self.assertEqual(
            CommunityAuditLog.objects.count(),
            audit_count_before,
            "Empty email must not write an audit log row.",
        )

    def test_customer_updated_with_colliding_email_raises_permanent_error(self):
        """Another local user owns the email — raise WebhookPermanentError.

        The User model enforces ``unique=True`` on email, so silently
        catching the IntegrityError would either swallow a real problem
        or produce a confusing 500. WebhookPermanentError is the
        contract: the dispatcher records a ``failed_permanent`` row,
        Stripe stops retrying, and on-call has a row to investigate.
        """
        owner = User.objects.create_user(email="owner@example.com")
        owner.stripe_customer_id = "cus_owner_1"
        owner.save(update_fields=["stripe_customer_id"])

        # The user Stripe is updating: a DIFFERENT account whose Stripe
        # customer just had its email changed to "owner@example.com".
        editor = User.objects.create_user(email="editor@example.com")
        editor.stripe_customer_id = "cus_editor_1"
        editor.save(update_fields=["stripe_customer_id"])

        with self.assertRaises(WebhookPermanentError):
            handle_customer_updated(
                _make_customer_payload(
                    customer_id="cus_editor_1",
                    email="owner@example.com",
                )
            )

        editor.refresh_from_db()
        owner.refresh_from_db()
        # Critical invariant: neither user's email changed. The handler
        # raised BEFORE writing — no half-applied state.
        self.assertEqual(editor.email, "editor@example.com")
        self.assertEqual(owner.email, "owner@example.com")

    def test_customer_updated_event_appears_in_event_handlers_map(self):
        """The dispatcher must wire ``customer.updated`` to this handler.

        Pure registration check — if a future refactor drops the key
        from EVENT_HANDLERS, the live Stripe endpoint will silently
        ignore real ``customer.updated`` events and the email-sync
        feature disappears without any test catching it.
        """
        self.assertIn("customer.updated", EVENT_HANDLERS)
        self.assertIs(EVENT_HANDLERS["customer.updated"], handle_customer_updated)

    def test_customer_updated_does_not_sync_name_field(self):
        """Locks the email-only scope.

        ``customer.updated`` can carry ``name``, ``metadata``, ``phone``,
        ``address``, etc. The local ``User`` model has no name field
        today, and we explicitly don't want this handler to grow into
        a general-purpose profile sync. If someone later adds a name
        field to User, this test should still pass (the handler must
        not touch it) — and the test acts as a forcing function to
        write a separate ``handle_customer_updated_name_sync`` issue
        rather than quietly stuffing fields into this one.
        """
        user = User.objects.create_user(email="scope@example.com")
        user.stripe_customer_id = "cus_scope_1"
        user.save(update_fields=["stripe_customer_id"])

        # Send a payload with name / metadata / phone — the handler
        # MUST only touch email and ignore the rest.
        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_scope_1",
                email="scope-new@example.com",
                name="Should Be Ignored",
                metadata={"should_be_ignored": "yes"},
                phone="+15551234567",
            )
        )

        user.refresh_from_db()
        # Email was synced (proving the handler ran).
        self.assertEqual(user.email, "scope-new@example.com")
        # The audit log row records ONLY the email-related fields —
        # no name / metadata / phone leak in.
        log = CommunityAuditLog.objects.filter(
            user=user,
            action="email_synced_from_stripe",
        ).latest("timestamp")
        details = json.loads(log.details)
        self.assertNotIn("name", details)
        self.assertNotIn("metadata", details)
        self.assertNotIn("phone", details)

    def test_customer_updated_writes_audit_log_on_real_change(self):
        """A real email change writes one ``CommunityAuditLog`` row.

        The row must carry the action slug, both old and new emails
        (so on-call can reconstruct what happened), and the reason
        ``customer_updated`` to distinguish from any future Stripe
        email sync paths.
        """
        user = User.objects.create_user(email="before@example.com")
        user.stripe_customer_id = "cus_audit_1"
        user.save(update_fields=["stripe_customer_id"])

        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_audit_1",
                email="after@example.com",
            )
        )

        logs = CommunityAuditLog.objects.filter(
            user=user,
            action="email_synced_from_stripe",
        )
        self.assertEqual(logs.count(), 1)
        details = json.loads(logs.first().details)
        self.assertEqual(details["status"], "ok")
        self.assertEqual(details["reason"], "customer_updated")
        self.assertEqual(details["old_email"], "before@example.com")
        self.assertEqual(details["new_email"], "after@example.com")

    def test_customer_updated_writes_no_audit_log_on_noop(self):
        """No-op paths must NOT write an audit log row.

        Three distinct no-op shapes share this assertion:

        1. Email matches what Stripe sent.
        2. Email is empty in the payload.
        3. The local user does not exist for this customer id.

        Writing an audit row in any of these would pollute the table
        with one row per Stripe webhook delivery (Stripe fires
        ``customer.updated`` on lots of unrelated edits in the portal).
        """
        user = User.objects.create_user(email="noop@example.com")
        user.stripe_customer_id = "cus_noop_1"
        user.save(update_fields=["stripe_customer_id"])
        audit_count_before = CommunityAuditLog.objects.count()

        # 1. matching email
        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_noop_1",
                email="noop@example.com",
            )
        )
        # 2. empty email
        handle_customer_updated(
            _make_customer_payload(customer_id="cus_noop_1", email="")
        )
        # 3. unknown customer
        handle_customer_updated(
            _make_customer_payload(
                customer_id="cus_does_not_exist",
                email="someone@example.com",
            )
        )

        self.assertEqual(
            CommunityAuditLog.objects.count(),
            audit_count_before,
            "No-op customer.updated paths must not write an audit log row.",
        )
