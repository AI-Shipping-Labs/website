import re

_HEADING_WEEK = re.compile(r'^\s*week\s+(\d+)\b', re.IGNORECASE)
_TITLE_WEEK = re.compile(r'^\s*week\s+(\d+)\s*[·•:|\-–—]+\s*(.*)$', re.IGNORECASE | re.DOTALL)


def title_under_heading(title, heading):
    if not title or not heading:
        return title
    heading_match = _HEADING_WEEK.match(heading)
    title_match = _TITLE_WEEK.match(title)
    if heading_match is None or title_match is None:
        return title
    if int(heading_match.group(1)) != int(title_match.group(1)):
        return title
    remainder = title_match.group(2).strip()
    return remainder or title


def section_heading_for(module, siblings):
    heading = ''
    for sibling in siblings:
        if sibling.syllabus_section:
            heading = sibling.syllabus_section
        if sibling.pk == module.pk:
            return heading
    return ''
