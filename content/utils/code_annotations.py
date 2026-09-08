"""Parse and render structured code annotations in course unit Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

import yaml
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import AnchorToken, KeyToken, ScalarToken, TagToken

from content.utils.linkify import linkify_urls
from content.utils.markdown import render_markdown


class CodeAnnotationError(ValueError):
    """A course-unit code annotation payload is invalid."""


@dataclass(frozen=True)
class CodeAnnotation:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Fence:
    start: int
    end: int
    language: str
    ordinary: bool


@dataclass(frozen=True)
class CourseUnitBody:
    markdown: str
    annotations_by_block: tuple[tuple[CodeAnnotation, ...] | None, ...]


_FENCE_OPEN_RE = re.compile(
    r'^[ \t]{0,3}(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)$'
)
_COMMENT_OPEN_RE = re.compile(r'^[ \t]*<!--[ \t]*$')
_COMMENT_CLOSE_RE = re.compile(r'^[ \t]*--\>[ \t]*$')
_RESERVED_KEY_RE = re.compile(
    r'(?:(?P<quote>["\'])(?:structured|code_annotations)(?P=quote)'
    r'|\b(?:structured|code_annotations))\s*:'
)
_LINES_RE = re.compile(r'^(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)$')
_CODEHILITE_RE = re.compile(
    r'<div class="codehilite">(?P<contents>.*?)</div>',
    re.DOTALL,
)
_SPECIAL_FENCE_LANGUAGES = frozenset({'eventwidget', 'mermaid'})


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                'while constructing a mapping',
                node.start_mark,
                'found an unhashable mapping key',
                key_node.start_mark,
            ) from exc
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                'while constructing a mapping',
                node.start_mark,
                f'found duplicate key {key!r}',
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _line_text(line):
    return line.rstrip('\r\n')


def _fence_open(line):
    match = _FENCE_OPEN_RE.match(_line_text(line))
    if not match:
        return None
    marker = match.group('marker')
    info = match.group('info').strip()
    language = info.split(None, 1)[0] if info else ''
    if language.startswith('{'):
        attr_match = re.search(
            r'\.([A-Za-z0-9_+-]+)(?=[\s}])',
            info,
        )
        language = attr_match.group(1) if attr_match else ''
    return marker, language


def _fence_close(line, marker):
    text = _line_text(line)
    return bool(re.match(
        rf'^[ \t]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$',
        text,
    ))


def _scan_blocks_and_comments(lines):
    fences = []
    comments = []
    i = 0
    while i < len(lines):
        fence = _fence_open(lines[i])
        if fence:
            marker, language = fence
            end = i
            for candidate_end in range(i + 1, len(lines)):
                if _fence_close(lines[candidate_end], marker):
                    end = candidate_end
                    break
            if end == i:
                # An unclosed fence owns the rest of the document. A comment
                # in it is executable/displayed code, not metadata.
                break
            fences.append(_Fence(
                start=i,
                end=end,
                language=language,
                ordinary=not (
                    marker == '```'
                    and language.lower() in _SPECIAL_FENCE_LANGUAGES
                ),
            ))
            i = end + 1
            continue

        if _COMMENT_OPEN_RE.match(_line_text(lines[i])):
            comment_end = None
            for candidate_end in range(i + 1, len(lines)):
                if _COMMENT_CLOSE_RE.match(_line_text(lines[candidate_end])):
                    comment_end = candidate_end
                    break
            if comment_end is not None:
                payload = ''.join(lines[i + 1:comment_end])
                comments.append((i, comment_end, payload))
                i = comment_end + 1
                continue
            payload = ''.join(lines[i + 1:])
            if _looks_like_candidate(payload):
                raise CodeAnnotationError(
                    f'unclosed structured code metadata near source line '
                    f'{i + 1}: expected a standalone --> line'
                )
        elif '<!--' in lines[i] and _RESERVED_KEY_RE.search(lines[i]):
            raise CodeAnnotationError(
                f'structured code metadata near source line {i + 1} must use '
                'a standalone multi-line HTML comment'
            )
        i += 1
    return fences, comments


def _looks_like_candidate(payload):
    try:
        root = yaml.compose(payload, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return _malformed_payload_has_root_key(payload)
    if not isinstance(root, MappingNode):
        return False
    root_keys = {
        key_node.value
        for key_node, _value_node in root.value
        if isinstance(key_node, ScalarNode)
    }
    return bool(root_keys & {'structured', 'code_annotations'})


def _malformed_payload_has_root_key(payload):
    """Keep malformed annotation YAML visible to validation.

    Composition is authoritative for valid YAML. If composition fails, scan
    the key tokens produced before the syntax error and treat reserved keys at
    the document's shallowest mapping indentation as candidate metadata. This
    preserves useful errors for a broken root payload without promoting a
    nested ``structured`` key in an unrelated comment.
    """
    keys = []
    pending_key_column = None
    try:
        tokens = yaml.scan(payload, Loader=yaml.SafeLoader)
        for token in tokens:
            if isinstance(token, KeyToken):
                pending_key_column = token.start_mark.column
            elif pending_key_column is not None:
                if isinstance(token, ScalarToken):
                    keys.append((pending_key_column, token.value))
                    pending_key_column = None
                elif not isinstance(
                    token,
                    (TagToken, AnchorToken),
                ):
                    pending_key_column = None
    except yaml.YAMLError:
        pass
    if not keys:
        return False
    root_column = min(column for column, _key in keys)
    return any(
        column == root_column and key in {'structured', 'code_annotations'}
        for column, key in keys
    )


def _parse_payload(payload, source_line):
    loader = _StrictSafeLoader(payload)
    try:
        data = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise CodeAnnotationError(
            f'invalid YAML near source line {source_line}: {exc}'
        ) from exc
    finally:
        loader.dispose()

    if not isinstance(data, dict):
        raise CodeAnnotationError(
            f'payload near source line {source_line} must be a mapping'
        )
    if set(data) != {'structured', 'code_annotations'}:
        unknown = sorted(
            (str(key) for key in set(data) - {'structured', 'code_annotations'})
        )
        missing = sorted(
            str(key) for key in {'structured', 'code_annotations'} - set(data)
        )
        details = []
        if missing:
            details.append(f'missing {", ".join(missing)}')
        if unknown:
            details.append(f'unknown key(s): {", ".join(unknown)}')
        raise CodeAnnotationError(
            f'invalid payload near source line {source_line}: '
            + '; '.join(details)
        )
    if data['structured'] is not True:
        raise CodeAnnotationError(
            f'payload near source line {source_line}: structured must be true'
        )

    raw_annotations = data['code_annotations']
    if not isinstance(raw_annotations, list) or not raw_annotations:
        raise CodeAnnotationError(
            f'payload near source line {source_line}: '
            'code_annotations must be a non-empty sequence'
        )

    _validate_quoted_range_nodes(payload, source_line)

    annotations = []
    for index, raw in enumerate(raw_annotations, start=1):
        if not isinstance(raw, dict):
            raise CodeAnnotationError(
                f'payload near source line {source_line}, annotation {index}: '
                'item must be a mapping'
            )
        keys = set(raw)
        selector_keys = keys & {'line', 'lines'}
        if keys - {'line', 'lines', 'text'}:
            unknown = sorted(
                str(key) for key in keys - {'line', 'lines', 'text'}
            )
            raise CodeAnnotationError(
                f'payload near source line {source_line}, annotation {index}: '
                f'unknown key(s): {", ".join(unknown)}'
            )
        if selector_keys != {'line'} and selector_keys != {'lines'}:
            raise CodeAnnotationError(
                f'payload near source line {source_line}, annotation {index}: '
                'use exactly one of line or lines'
            )
        text = raw.get('text')
        if not isinstance(text, str) or not text.strip():
            raise CodeAnnotationError(
                f'payload near source line {source_line}, annotation {index}: '
                'text must be a non-empty string'
            )
        text = re.sub(r'\s+', ' ', text).strip()

        if 'line' in raw:
            line = raw['line']
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                raise CodeAnnotationError(
                    f'payload near source line {source_line}, annotation '
                    f'{index}: line must be a positive integer'
                )
            annotations.append(CodeAnnotation(line, line, text))
            continue

        lines_value = raw['lines']
        match = (
            _LINES_RE.match(lines_value)
            if isinstance(lines_value, str) else None
        )
        if not match:
            raise CodeAnnotationError(
                f'payload near source line {source_line}, annotation {index}: '
                'lines must be a quoted positive start-end range'
            )
        start = int(match.group('start'))
        end = int(match.group('end'))
        if start >= end:
            raise CodeAnnotationError(
                f'payload near source line {source_line}, annotation {index}: '
                'range start must be less than range end'
            )
        annotations.append(CodeAnnotation(start, end, text))
    return tuple(annotations)


def _validate_quoted_range_nodes(payload, source_line):
    """Reject plain-style ``lines`` scalars such as ``lines: 2-4``."""
    root = yaml.compose(payload, Loader=yaml.SafeLoader)
    if not isinstance(root, MappingNode):
        return
    annotations_node = None
    for key_node, value_node in root.value:
        if isinstance(key_node, ScalarNode) and key_node.value == 'code_annotations':
            annotations_node = value_node
            break
    if not isinstance(annotations_node, SequenceNode):
        return
    for index, item_node in enumerate(annotations_node.value, start=1):
        if not isinstance(item_node, MappingNode):
            continue
        for key_node, value_node in item_node.value:
            if (
                isinstance(key_node, ScalarNode)
                and key_node.value == 'lines'
                and (
                    not isinstance(value_node, ScalarNode)
                    or value_node.style not in {"'", '"'}
                )
            ):
                raise CodeAnnotationError(
                    f'payload near source line {source_line}, annotation '
                    f'{index}: lines must be a quoted positive start-end range'
                )


def _associated_fence(comment_start, fences, lines):
    previous = None
    for fence in fences:
        if fence.end < comment_start:
            previous = fence
        else:
            break
    if previous is None:
        return None
    gap = ''.join(lines[previous.end + 1:comment_start])
    if gap.strip():
        return None
    return previous


def _previous_fence(comment_start, fences):
    return next(
        (fence for fence in reversed(fences) if fence.end < comment_start),
        None,
    )


def _validate_ranges(annotations, fence, lines, source_line):
    code_line_count = fence.end - fence.start - 1
    occupied = set()
    for annotation in annotations:
        if annotation.end > code_line_count:
            raise CodeAnnotationError(
                f'annotation near source line {source_line} points to line '
                f'{annotation.end}, but the code block has '
                f'{code_line_count} visible lines'
            )
        current = set(range(annotation.start, annotation.end + 1))
        if occupied & current:
            raise CodeAnnotationError(
                f'annotation near source line {source_line} overlaps or '
                'repeats another annotation range'
            )
        occupied.update(current)


def parse_course_unit_body(body):
    """Validate and strip structured metadata from a course unit body.

    The returned block list is ordered like ordinary fenced code blocks in the
    stripped Markdown. ``None`` marks an unannotated block.
    """
    if not body:
        return CourseUnitBody('', ())

    lines = body.splitlines(keepends=True)
    fences, comments = _scan_blocks_and_comments(lines)
    ordinary_fences = [fence for fence in fences if fence.ordinary]
    annotations_by_block = [None] * len(ordinary_fences)
    remove_spans = []
    claimed_comment_ends = {}

    for comment_start, comment_end, payload in comments:
        if not _looks_like_candidate(payload):
            continue
        source_line = comment_start + 1
        annotations = _parse_payload(payload, source_line)
        previous_fence = _previous_fence(comment_start, fences)
        previous_comment_end = claimed_comment_ends.get(previous_fence)
        if (
            previous_comment_end is not None
            and not ''.join(lines[previous_comment_end + 1:comment_start]).strip()
        ):
            raise CodeAnnotationError(
                f'duplicate structured code metadata near source line '
                f'{source_line}: one payload per code block is allowed'
            )
        fence = _associated_fence(comment_start, fences, lines)
        if fence is None:
            raise CodeAnnotationError(
                f'orphaned structured code metadata near source line '
                f'{source_line}: it must immediately follow a code fence'
            )
        if not fence.ordinary:
            raise CodeAnnotationError(
                f'structured code metadata near source line {source_line}: '
                'special fences cannot have annotations'
            )
        _validate_ranges(annotations, fence, lines, source_line)
        block_index = ordinary_fences.index(fence)
        if annotations_by_block[block_index] is not None:
            raise CodeAnnotationError(
                f'duplicate structured code metadata near source line '
                f'{source_line}: one payload per code block is allowed'
            )
        annotations_by_block[block_index] = annotations
        claimed_comment_ends[fence] = comment_end
        remove_spans.append((comment_start, comment_end))

    if not remove_spans:
        return CourseUnitBody(body, tuple(annotations_by_block))
    remove_lines = {
        line_number
        for start, end in remove_spans
        for line_number in range(start, end + 1)
    }
    stripped = ''.join(
        line for index, line in enumerate(lines) if index not in remove_lines
    )
    return CourseUnitBody(stripped, tuple(annotations_by_block))


def _source_code(lines, fence):
    """Return the authored code without the fence-separating newline."""
    code = ''.join(lines[fence.start + 1:fence.end])
    if code.endswith('\r\n'):
        return code[:-2]
    if code.endswith(('\n', '\r')):
        return code[:-1]
    return code


def _highlight_code(code, language):
    if language:
        try:
            lexer = get_lexer_by_name(language)
        except ClassNotFound:
            lexer = TextLexer()
    else:
        lexer = TextLexer()
    highlighted = highlight(code, lexer, HtmlFormatter(nowrap=True))
    # Pygments appends a newline when the source does not have one. Remove
    # only that formatter-added character; a deliberate blank source line is
    # part of the authored code and must remain a visible numbered line.
    if highlighted.endswith('\n') and not code.endswith(('\n', '\r')):
        highlighted = highlighted[:-1]
    return highlighted.split('\n')


def _render_annotated_block(lines, fence, annotations, block_id):
    code = _source_code(lines, fence)
    highlighted_lines = _highlight_code(code, fence.language)
    highlighted_lines += [''] * (fence.end - fence.start - 1 - len(highlighted_lines))
    highlighted_lines = highlighted_lines[:fence.end - fence.start - 1]
    highlighted_numbers = {
        number
        for annotation in annotations
        for number in range(annotation.start, annotation.end + 1)
    }
    gutter = ''.join(
        f'<span class="code-line-number">{number}</span>'
        for number in range(1, len(highlighted_lines) + 1)
    )
    code_lines = '\n'.join(
        f'<span class="code-annotation-line{(" is-highlighted" if number in highlighted_numbers else "")}" '
        f'data-line-number="{number}">{line}</span>'
        for number, line in enumerate(highlighted_lines, start=1)
    )
    notes = []
    for annotation in annotations:
        if annotation.start == annotation.end:
            label = f'Line {annotation.start}'
        else:
            label = f'Lines {annotation.start}\N{EN DASH}{annotation.end}'
        notes.append(
            '<li class="code-annotation-note" data-testid="code-annotation-note">'
            f'<span class="code-annotation-label">{label}:</span>'
            f'<span class="code-annotation-text">{escape(annotation.text)}</span>'
            '</li>'
        )
    heading_id = f'code-annotations-{block_id}'
    notes_html = (
        f'<aside class="code-annotations" aria-labelledby="{heading_id}" '
        'data-testid="code-annotations">'
        f'<h3 id="{heading_id}" class="code-annotations-heading">'
        'Code annotations</h3>'
        '<ol class="code-annotation-list">'
        + ''.join(notes)
        + '</ol></aside>'
    )
    return (
        '<div class="codehilite annotated-code-block">'
        '<pre><span class="code-line-gutter" aria-hidden="true">'
        f'{gutter}</span><code>{code_lines}</code></pre>'
        f'</div>{notes_html}'
    )


def render_course_unit_body(body):
    """Render a course-unit body, adding UI chrome for valid annotations."""
    parsed = parse_course_unit_body(body)
    # Preserve the Unit renderer's existing bare-URL behavior in lesson prose,
    # but run it before inserting notes. Annotation text is an escaped plain-
    # text field, so URL-looking or Markdown-looking note content must stay
    # literal rather than being interpreted by the surrounding renderer.
    rendered = linkify_urls(render_markdown(parsed.markdown))
    if not any(parsed.annotations_by_block):
        return rendered

    source_lines = body.splitlines(keepends=True)
    source_fences, _ = _scan_blocks_and_comments(source_lines)
    ordinary_fences = [fence for fence in source_fences if fence.ordinary]
    ordinary_index = 0

    def replace(match):
        nonlocal ordinary_index
        annotations = parsed.annotations_by_block[ordinary_index]
        if annotations is not None:
            fence = ordinary_fences[ordinary_index]
            result = _render_annotated_block(
                source_lines, fence, annotations, ordinary_index + 1,
            )
        else:
            result = match.group(0)
        ordinary_index += 1
        return result

    return _CODEHILITE_RE.sub(replace, rendered)
