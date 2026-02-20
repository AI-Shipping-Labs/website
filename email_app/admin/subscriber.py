"""Admin for viewing newsletter subscribers with filtering and CSV export."""

import csv

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.http import HttpResponse

User = get_user_model()


class SubscriberStatusFilter(admin.SimpleListFilter):
    """Filter subscribers by their email/subscription status."""

    title = "subscription status"
    parameter_name = "sub_status"

    def lookups(self, request, model_admin):
        return [
            ("verified", "Verified & Subscribed"),
            ("unverified", "Unverified"),
            ("unsubscribed", "Unsubscribed"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "verified":
            return queryset.filter(email_verified=True, unsubscribed=False)
        if self.value() == "unverified":
            return queryset.filter(email_verified=False)
        if self.value() == "unsubscribed":
            return queryset.filter(unsubscribed=True)
        return queryset


class Subscriber(User):
    """Proxy model for viewing users as newsletter subscribers."""

    class Meta:
        proxy = True
        verbose_name = "Subscriber"
        verbose_name_plural = "Subscribers"


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    """Admin view for newsletter subscribers.

    Shows users as subscribers with status filters and CSV export.
    """

    list_display = [
        "email",
        "email_verified",
        "unsubscribed",
        "tier",
        "date_joined",
    ]
    list_filter = [
        SubscriberStatusFilter,
        "email_verified",
        "unsubscribed",
    ]
    search_fields = ["email", "first_name", "last_name"]
    ordering = ["-date_joined"]
    readonly_fields = [
        "email",
        "email_verified",
        "unsubscribed",
        "tier",
        "date_joined",
    ]
    actions = ["export_csv"]

    def has_add_permission(self, request):
        """Subscribers are created via the subscribe API, not admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Don't allow deleting users from subscriber view."""
        return False

    @admin.action(description="Export selected subscribers as CSV")
    def export_csv(self, request, queryset):
        """Export selected subscribers to CSV file."""
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="subscribers.csv"'
        )

        writer = csv.writer(response)
        writer.writerow([
            "Email",
            "First Name",
            "Last Name",
            "Email Verified",
            "Unsubscribed",
            "Tier",
            "Date Joined",
        ])

        for user in queryset.order_by("-date_joined"):
            writer.writerow([
                user.email,
                user.first_name,
                user.last_name,
                user.email_verified,
                user.unsubscribed,
                user.tier.name if user.tier else "Free",
                user.date_joined.strftime("%Y-%m-%d %H:%M:%S"),
            ])

        return response
