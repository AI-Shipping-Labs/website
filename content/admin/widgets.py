"""Custom admin widgets for the content app."""

import json

from django.forms import Widget


class TimestampEditorWidget(Widget):
    """
    Admin widget for editing video timestamps as a list of
    {time_seconds, label} entries.

    Renders a table with add/remove/reorder controls and
    time (MM:SS) + label inputs per row.
    """

    template_name = 'admin/widgets/timestamp_editor.html'

    def format_value(self, value):
        """Ensure value is a JSON string for the template."""
        if value is None:
            return '[]'
        if isinstance(value, str):
            # Validate it's valid JSON
            try:
                json.loads(value)
                return value
            except (json.JSONDecodeError, TypeError):
                return '[]'
        # It's already a Python object (list), serialize it
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return '[]'

    def value_from_datadict(self, data, files, name):
        """Extract value from POST data."""
        value = data.get(name, '[]')
        return value

    class Media:
        css = {}
        js = ()
