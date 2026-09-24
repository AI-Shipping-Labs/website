"""Bind authored homework markdown sections to stable question identities."""

import re

QUESTION_HEADING = re.compile(r'^##\s+Question\s+(\d+)(?:[.:-]|\s|$)', re.IGNORECASE)
SECTION_HEADING = re.compile(r'^##\s+')
QUESTION_ID = re.compile(r'^q(\d+)(?:[-_].*)?$', re.IGNORECASE)
STEP_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
FENCE = re.compile(r'^\s*(`{3,}|~{3,})')


def split_homework_sections(markdown):
    """Return introduction, numbered prompt sections, and closing guidance.

    A fenced code example can contain heading-shaped lines; those remain in
    the surrounding section rather than becoming a navigation step.
    """
    introduction = []
    questions = []
    closing = []
    target = introduction
    fence_char = None
    fence_length = 0
    for line in markdown.splitlines(keepends=True):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
        elif fence_char is None and SECTION_HEADING.match(line):
            match = QUESTION_HEADING.match(line)
            if match:
                questions.append((int(match.group(1)), []))
                target = questions[-1][1]
            elif questions:
                target = closing
        target.append(line)
    return (
        ''.join(introduction).strip(),
        [(number, ''.join(lines).strip()) for number, lines in questions],
        ''.join(closing).strip(),
    )


def split_out_named_section(markdown, title):
    """Return ``(remaining, section_body)`` for one authored H2 section.

    Heading-shaped lines in fenced examples remain part of their surrounding
    section, matching :func:`split_homework_sections`.
    """
    remaining = []
    section = []
    in_section = False
    found = False
    fence_char = None
    fence_length = 0
    expected = ' '.join(title.split()).casefold()

    for line in markdown.splitlines(keepends=True):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
            (section if in_section else remaining).append(line)
            continue

        if fence_char is None and SECTION_HEADING.match(line):
            if in_section:
                in_section = False
            heading = line.lstrip()[2:].strip()
            if ' '.join(heading.split()).casefold() == expected and not found:
                found = True
                in_section = True
                continue
        (section if in_section else remaining).append(line)

    return ''.join(remaining).strip(), ''.join(section).strip()


def validate_question_bindings(markdown, question_ids, source_path):
    """Fail import if a rich prompt could attach to the wrong answer record."""
    intro, sections, closing = split_homework_sections(markdown)
    if not question_ids or len(set(question_ids)) != len(question_ids):
        raise ValueError(
            f'Invalid homework steps in {source_path}: question ids must be unique'
        )
    if any(
        not isinstance(key, str) or not STEP_KEY.fullmatch(key)
        or key in {'intro', 'review'}
        for key in question_ids
    ):
        raise ValueError(
            f'Invalid homework steps in {source_path}: question ids must be '
            'stable slugs distinct from intro and review'
        )
    if not sections:
        return intro, {}, closing
    numbers = [number for number, _body in sections]
    expected = list(range(1, len(question_ids) + 1))
    if numbers != expected:
        raise ValueError(
            f'Invalid homework steps in {source_path}: Question headings '
            f'{numbers!r} do not match authored questions {expected!r}'
        )
    bindings = {}
    for number, question_id in enumerate(question_ids, start=1):
        match = QUESTION_ID.fullmatch(str(question_id))
        if match is None or int(match.group(1)) != number:
            raise ValueError(
                f'Invalid homework steps in {source_path}: questions[{number - 1}].id '
                f'{question_id!r} must identify Question {number} (q{number}-...)'
            )
        if question_id in bindings:
            raise ValueError(
                f'Invalid homework steps in {source_path}: duplicate question id '
                f'{question_id!r}'
            )
        bindings[question_id] = sections[number - 1][1]
    return intro, bindings, closing
