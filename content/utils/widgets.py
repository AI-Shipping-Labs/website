import re

from django.template.loader import render_to_string


def expand_widgets(html, data):
    """Replace widget markers with rendered template HTML.

    Finds all <!-- widget:X data=Y --> markers in the HTML and replaces
    each with the rendered output of templates/includes/widgets/X.html,
    passing data[Y] as the 'items' context variable.

    Args:
        html: HTML string containing widget markers.
        data: dict from frontmatter 'data' key.

    Returns:
        HTML string with all widget markers replaced by rendered templates.

    Raises:
        django.template.TemplateDoesNotExist: if widget template is missing.
        KeyError: if data key referenced in marker is not in data dict.
    """
    pattern = r'<!-- widget:(\w+) data=(\w+) -->'

    def replace(match):
        widget_name = match.group(1)
        data_key = match.group(2)
        template = f'includes/widgets/{widget_name}.html'

        if data_key not in data:
            raise KeyError(
                f"Widget '{widget_name}' references data key '{data_key}' "
                f"but it was not found in frontmatter data. "
                f"Available keys: {list(data.keys())}"
            )

        context = {'items': data[data_key]}
        return render_to_string(template, context)

    return re.sub(pattern, replace, html)
