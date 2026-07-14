"""Read-only recovery audit of legacy Stripe authentication aliases."""

import json
import re

from django.core.management.base import BaseCommand

from accounts.models import EmailAlias
from community.models import CommunityAuditLog
from payments.models import CheckoutFulfillment, PaymentAccountMismatch

SESSION_RE = re.compile(r"\bcs_[A-Za-z0-9_]+\b")


class Command(BaseCommand):
    help = "List legacy Stripe aliases with OAuth, Checkout, and audit provenance."

    def handle(self, *args, **options):
        aliases = EmailAlias.objects.filter(
            source=EmailAlias.SOURCE_STRIPE_RELAY,
            created_by__isnull=True,
        ).select_related("user").order_by("created_at", "pk")
        self.stdout.write(
            "alias_id\tuser_id\tuser_email\talias_email\toauth_identities\t"
            "checkout_sessions\tmismatches\taudit_context\tcreated_at"
        )
        for alias in aliases:
            oauth_identities = []
            for social in alias.user.socialaccount_set.order_by("provider", "pk"):
                extra = social.extra_data if isinstance(social.extra_data, dict) else {}
                oauth_identities.append({
                    "provider": social.provider,
                    "uid": social.uid,
                    "email": extra.get("email", ""),
                    "alias_matches_email": str(extra.get("email", "")).casefold()
                    == alias.email.casefold(),
                })

            mismatches = list(
                PaymentAccountMismatch.objects.filter(
                    stripe_email__iexact=alias.email,
                ).order_by("created_at", "pk")
            )
            audit_logs = list(
                CommunityAuditLog.objects.filter(
                    user=alias.user,
                    details__icontains=alias.email,
                ).order_by("timestamp", "pk")
            )
            session_ids = {row.stripe_session_id for row in mismatches}
            for log in audit_logs:
                session_ids.update(SESSION_RE.findall(log.details or ""))
            fulfillments = {
                row.stripe_session_id: row
                for row in CheckoutFulfillment.objects.filter(
                    stripe_session_id__in=session_ids,
                )
            }
            checkout_sessions = [
                {
                    "id": session_id,
                    "fulfillment_status": (
                        fulfillments[session_id].status
                        if session_id in fulfillments else "not_recorded"
                    ),
                    "fulfillment_reason": (
                        fulfillments[session_id].reason
                        if session_id in fulfillments else ""
                    ),
                }
                for session_id in sorted(session_ids)
            ]
            mismatch_context = [
                {
                    "id": row.pk,
                    "session": row.stripe_session_id,
                    "reason": row.reason,
                    "status": row.status,
                }
                for row in mismatches
            ]
            audit_context = [
                {
                    "id": row.pk,
                    "action": row.action,
                    "timestamp": row.timestamp.isoformat(),
                    "details": row.details,
                }
                for row in audit_logs
            ]
            self.stdout.write(
                f"{alias.pk}\t{alias.user_id}\t{alias.user.email}\t{alias.email}\t"
                f"{json.dumps(oauth_identities, sort_keys=True)}\t"
                f"{json.dumps(checkout_sessions, sort_keys=True)}\t"
                f"{json.dumps(mismatch_context, sort_keys=True)}\t"
                f"{json.dumps(audit_context, sort_keys=True)}\t"
                f"{alias.created_at.isoformat()}"
            )
        self.stdout.write(self.style.SUCCESS(f"Total legacy Stripe aliases: {aliases.count()}"))
