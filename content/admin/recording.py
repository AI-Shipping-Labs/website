import json

from django import forms
from django.contrib import admin

from content.admin.widgets import TimestampEditorWidget
from content.models import Recording


class RecordingAdminForm(forms.ModelForm):
    """Custom form for Recording that uses the TimestampEditorWidget."""

    class Meta:
        model = Recording
        fields = '__all__'
        widgets = {
            'timestamps': TimestampEditorWidget(),
        }

    def clean_timestamps(self):
        """Parse the JSON string back into a Python list."""
        value = self.cleaned_data.get('timestamps', '[]')
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return []


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    form = RecordingAdminForm
    list_display = ['title', 'date', 'required_level', 'published', 'published_at']
    list_filter = ['published', 'required_level', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['published_at']
