"""Studio forms for the triggers subsystem (issue #1070).

``TriggerSubscriptionForm`` treats ``secret`` as write-only: an existing
secret is never rendered back into the form (the field is blank on edit),
and leaving it blank on edit preserves the stored value. ``property_filter``
is entered as JSON text and validated to a dict.
"""

import json

from django import forms

from triggers.models import EVENT_TYPE_CHOICES, EventWidget, TriggerSubscription

_INPUT_CLASS = (
    "w-full bg-secondary border border-border rounded-lg px-4 py-2 text-sm "
    "text-foreground placeholder-muted-foreground focus:outline-none "
    "focus:ring-1 focus:ring-accent"
)


class TriggerSubscriptionForm(forms.ModelForm):
    """Create/edit a subscription. Secret is write-only."""

    property_filter = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
        help_text='Exact-match JSON, e.g. {"name": "v0_workshop"}. Empty matches all.',
    )
    secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "class": _INPUT_CLASS,
                "placeholder": "Leave blank to keep the current secret",
                "autocomplete": "new-password",
            },
        ),
        help_text="HMAC signing secret shared with the handler. Write-only.",
    )

    class Meta:
        model = TriggerSubscription
        fields = [
            "event_type",
            "property_filter",
            "target_url",
            "secret",
            "description",
            "is_active",
        ]
        widgets = {
            "event_type": forms.Select(
                choices=EVENT_TYPE_CHOICES, attrs={"class": _INPUT_CLASS},
            ),
            "target_url": forms.URLInput(attrs={"class": _INPUT_CLASS}),
            "description": forms.TextInput(attrs={"class": _INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Render existing JSON filter as text; never echo the secret.
        if self.instance and self.instance.pk:
            self.fields["property_filter"].initial = json.dumps(
                self.instance.property_filter or {},
            )

    def clean_property_filter(self):
        raw = (self.cleaned_data.get("property_filter") or "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise forms.ValidationError("Must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise forms.ValidationError("Must be a JSON object (key/value map).")
        return value

    def clean_secret(self):
        secret = self.cleaned_data.get("secret") or ""
        if not secret:
            if self.instance and self.instance.pk:
                # Keep the stored secret unchanged on edit.
                return self.instance.secret
            raise forms.ValidationError("A signing secret is required.")
        return secret


class EventWidgetForm(forms.ModelForm):
    class Meta:
        model = EventWidget
        fields = [
            "slug",
            "event_name",
            "min_level",
            "claim_label",
            "claim_body",
            "signin_cta",
            "claimed_label",
            "exhausted_label",
            "is_active",
        ]
        widgets = {
            "slug": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "event_name": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "min_level": forms.NumberInput(attrs={"class": _INPUT_CLASS}),
            "claim_label": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "claim_body": forms.Textarea(attrs={"class": _INPUT_CLASS, "rows": 3}),
            "signin_cta": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "claimed_label": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "exhausted_label": forms.TextInput(attrs={"class": _INPUT_CLASS}),
        }
