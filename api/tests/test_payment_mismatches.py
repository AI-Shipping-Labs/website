import json

from django.test import TestCase

from accounts.models import EmailAlias, Token, User
from community.models import CommunityAuditLog
from payments.models import PaymentAccountMismatch, Tier


class PaymentMismatchApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="mismatch-admin@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.admin, name="mismatch-bot")
        cls.non_staff = User.objects.create_user(
            email="mismatch-plain@test.com",
            password="testpass",
            is_staff=False,
        )
        cls.non_staff_token = Token(
            key="non-staff-key-1105",
            user=cls.non_staff,
            name="legacy",
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def _auth(self, token=None):
        if token is False:
            return {}
        key = token.key if token is not None else self.token.key
        return {"HTTP_AUTHORIZATION": f"Token {key}"}

    def _get(self, query="", *, token=None):
        return self.client.get(
            f"/api/users/payment-mismatches{query}",
            **self._auth(token),
        )

    def _patch(self, mismatch_id, payload, *, token=None):
        return self.client.patch(
            f"/api/users/payment-mismatches/{mismatch_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _mismatch(self, *, status=PaymentAccountMismatch.STATUS_OPEN):
        paid = User.objects.create_user(email=f"paid-{status}@test.com")
        candidate = User.objects.create_user(email=f"candidate-{status}@test.com")
        return PaymentAccountMismatch.objects.create(
            stripe_session_id=f"cs_{status}_1105",
            stripe_customer_id=f"cus_{status}_1105",
            stripe_subscription_id=f"sub_{status}_1105",
            stripe_email=candidate.email,
            paid_user=paid,
            candidate_user=candidate,
            reason=PaymentAccountMismatch.REASON_PRIMARY_EMAIL_COLLISION,
            status=status,
            resolution_note="done" if status != PaymentAccountMismatch.STATUS_OPEN else "",
        )

    def test_list_filters_by_status_email_customer_and_session(self):
        open_mismatch = self._mismatch(status=PaymentAccountMismatch.STATUS_OPEN)
        self._mismatch(status=PaymentAccountMismatch.STATUS_RESOLVED)

        response = self._get("?status=open")

        self.assertEqual(response.status_code, 200)
        rows = response.json()["payment_mismatches"]
        self.assertEqual([row["id"] for row in rows], [open_mismatch.pk])
        self.assertEqual(rows[0]["paid_user"]["email"], open_mismatch.paid_user.email)
        self.assertEqual(
            rows[0]["candidate_user"]["email"],
            open_mismatch.candidate_user.email,
        )

        by_email = self._get(f"?email={open_mismatch.stripe_email}")
        self.assertEqual(
            [row["id"] for row in by_email.json()["payment_mismatches"]],
            [open_mismatch.pk],
        )
        by_customer = self._get(
            f"?stripe_customer_id={open_mismatch.stripe_customer_id}"
        )
        self.assertEqual(
            [row["id"] for row in by_customer.json()["payment_mismatches"]],
            [open_mismatch.pk],
        )
        by_session = self._get(
            f"?stripe_session_id={open_mismatch.stripe_session_id}"
        )
        self.assertEqual(
            [row["id"] for row in by_session.json()["payment_mismatches"]],
            [open_mismatch.pk],
        )

    def test_patch_marks_resolved_with_required_note_and_audit(self):
        mismatch = self._mismatch()

        response = self._patch(
            mismatch.pk,
            {
                "status": "resolved",
                "resolution_note": "Merged after preview.",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "resolved")
        self.assertEqual(body["resolution_note"], "Merged after preview.")
        mismatch.refresh_from_db()
        self.assertEqual(mismatch.status, PaymentAccountMismatch.STATUS_RESOLVED)
        self.assertEqual(mismatch.resolved_by, self.admin)
        self.assertEqual(
            CommunityAuditLog.objects.filter(
                user=mismatch.paid_user,
                action="payment_mismatch_updated",
            ).count(),
            1,
        )

    def test_patch_ignored_never_merges_or_moves_aliases(self):
        mismatch = self._mismatch()
        paid = mismatch.paid_user
        candidate = mismatch.candidate_user
        candidate.tier = Tier.objects.get(slug="free")
        candidate.save(update_fields=["tier"])
        alias = EmailAlias.objects.create(
            user=candidate,
            email="candidate-relay-1105@test.com",
        )

        response = self._patch(
            mismatch.pk,
            {
                "status": "ignored",
                "resolution_note": "Not the same person.",
            },
        )

        self.assertEqual(response.status_code, 200)
        paid.refresh_from_db()
        candidate.refresh_from_db()
        alias.refresh_from_db()
        self.assertTrue(paid.is_active)
        self.assertTrue(candidate.is_active)
        self.assertEqual(alias.user, candidate)
        self.assertEqual(candidate.tier.slug, "free")

    def test_patch_rejects_invalid_status_empty_note_and_unknown_field(self):
        mismatch = self._mismatch()

        invalid = self._patch(mismatch.pk, {"status": "open", "resolution_note": "x"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "invalid_status")

        empty_note = self._patch(
            mismatch.pk,
            {"status": "resolved", "resolution_note": " "},
        )
        self.assertEqual(empty_note.status_code, 422)
        self.assertEqual(empty_note.json()["code"], "validation_error")

        unknown = self._patch(
            mismatch.pk,
            {"status": "resolved", "resolution_note": "x", "merge": True},
        )
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unknown.json()["code"], "unknown_field")
        mismatch.refresh_from_db()
        self.assertEqual(mismatch.status, PaymentAccountMismatch.STATUS_OPEN)

    def test_auth_required_and_non_staff_token_rejected(self):
        self._mismatch()

        unauthenticated = self._get(token=False)
        non_staff = self._get(token=self.non_staff_token)

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(non_staff.status_code, 401)
