"""Validation for Studio and API Call profile writes (#1404)."""

from django import forms
from django.utils.text import slugify

from community.models import CallHost, is_usable_http_url

INPUT_CLASSES = (
    'w-full bg-secondary border border-border rounded-lg px-4 py-2 '
    'text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent'
)


class CallProfileForm(forms.ModelForm):
    """One validation contract for creating and editing CallHost rows."""

    slug = forms.SlugField(
        required=False,
        error_messages={
            'unique': 'A call profile with this slug already exists.',
        },
        widget=forms.TextInput(attrs={
            'class': INPUT_CLASSES,
            'placeholder': 'Generated from the name when left blank',
        }),
    )
    photo_url = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={
            'class': INPUT_CLASSES,
            'placeholder': 'https://example.com/photo.jpg',
        }),
    )
    booking_url = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={
            'class': INPUT_CLASSES,
            'placeholder': 'https://calendar.app.google/...',
        }),
    )
    order = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASSES, 'min': '0'}),
    )

    class Meta:
        model = CallHost
        fields = [
            'name',
            'slug',
            'role_label',
            'photo_url',
            'booking_url',
            'order',
            'is_active',
        ]
        labels = {
            'order': 'Display order',
            'is_active': 'Show on Request a call',
        }
        error_messages = {
            'slug': {
                'unique': 'A call profile with this slug already exists.',
            },
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': INPUT_CLASSES}),
            'role_label': forms.TextInput(attrs={'class': INPUT_CLASSES}),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 rounded border-border bg-secondary text-accent focus:ring-accent',
            }),
        }

    def clean_slug(self):
        slug = self.cleaned_data.get('slug', '')
        if not slug:
            slug = slugify(self.cleaned_data.get('name', ''))
        if not slug:
            raise forms.ValidationError('Slug is required.')
        return slug

    def _clean_http_url(self, field):
        value = self.cleaned_data.get(field, '')
        if value and not is_usable_http_url(value):
            raise forms.ValidationError('Enter a valid URL.')
        return value

    def clean_photo_url(self):
        return self._clean_http_url('photo_url')

    def clean_booking_url(self):
        return self._clean_http_url('booking_url')

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get('is_active')
            and 'booking_url' in cleaned_data
            and not cleaned_data.get('booking_url')
        ):
            self.add_error(
                'booking_url',
                'Booking URL is required when Show on Request a call is enabled.',
            )
        return cleaned_data
