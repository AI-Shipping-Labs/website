"""Template helpers for interactive, authored homework question prompts."""

import re
from html import unescape
from html.parser import HTMLParser

from django import template
from django.utils.html import escape, strip_tags
from django.utils.safestring import SafeData, mark_safe

register = template.Library()


@register.filter
def homework_step_nav_title(title, url):
    """Give the authored Learning in Public step its actual navigation name."""
    if 'homework_step=learning-in-public' in str(url):
        return 'Learning in Public'
    return title

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_OPTION_CONTROL_CLASS = "homework-option-control h-4 w-4 shrink-0 border-border text-accent"


class _Raw:
    def __init__(self, value):
        self.value = value


class _Element:
    def __init__(self, tag, start):
        self.tag = tag
        self.start = start
        self.end = ""
        self.children = []


class _FragmentParser(HTMLParser):
    """Keep Markdown HTML intact while exposing list items for replacement."""

    def __init__(self, fragment):
        super().__init__(convert_charrefs=False)
        self.roots = []
        self.open_elements = []
        self.feed(fragment)
        self.close()

    def _current_children(self):
        return self.open_elements[-1].children if self.open_elements else self.roots

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, self.get_starttag_text())
        self._current_children().append(element)
        if tag not in _VOID_TAGS:
            self.open_elements.append(element)

    def handle_startendtag(self, tag, attrs):
        self._current_children().append(_Raw(self.get_starttag_text()))

    def handle_endtag(self, tag):
        for index in range(len(self.open_elements) - 1, -1, -1):
            if self.open_elements[index].tag == tag:
                self.open_elements[index].end = f"</{tag}>"
                del self.open_elements[index:]
                return
        self._current_children().append(_Raw(f"</{tag}>"))

    def handle_data(self, data):
        self._current_children().append(_Raw(data))

    def handle_entityref(self, name):
        self._current_children().append(_Raw(f"&{name};"))

    def handle_charref(self, name):
        self._current_children().append(_Raw(f"&#{name};"))

    def handle_comment(self, data):
        self._current_children().append(_Raw(f"<!--{data}-->"))

    def handle_decl(self, decl):
        self._current_children().append(_Raw(f"<!{decl}>"))


def _serialize(node):
    if isinstance(node, _Raw):
        return node.value
    return node.start + "".join(_serialize(child) for child in node.children) + node.end


def _text_content(node):
    if isinstance(node, _Raw):
        return unescape(strip_tags(node.value))
    return "".join(_text_content(child) for child in node.children)


def _normalized_label(value):
    return re.sub(r"\s+", " ", unescape(strip_tags(value))).strip().casefold()


def _walk_elements(nodes):
    for node in nodes:
        if isinstance(node, _Element):
            yield node
            yield from _walk_elements(node.children)


def _append_attribute(start_tag, name, value):
    suffix = "/>" if start_tag.endswith("/>") else ">"
    return f'{start_tag[:-len(suffix)]} {name}="{value}"{suffix}'


def _input_html(control_type, key, selected):
    value = escape(str(key))
    checked = " checked" if selected else ""
    rounded = " rounded" if control_type == "checkbox" else ""
    return (
        f'<input type="{control_type}" name="answer" value="{value}" '
        f'class="{_OPTION_CONTROL_CLASS}{rounded}"{checked}>'
    )


def _replace_option_item(item, option, control_type):
    item.start = _append_attribute(item.start, "data-homework-option", "true")
    content = "".join(_serialize(child) for child in item.children)
    item.children = [
        _Raw(
            '<label class="homework-option-label">'
            + _input_html(control_type, option[0].key, option[1])
            + '<div class="homework-option-content">'
        ),
        _Raw(content),
        _Raw("</div></label>"),
    ]


def _matching_option_list(roots, options):
    expected = {_normalized_label(option.label): option for option, _selected in options}
    best = None
    for element in _walk_elements(roots):
        if element.tag not in {"ol", "ul"}:
            continue
        items = [
            child for child in element.children
            if isinstance(child, _Element) and child.tag == "li"
        ]
        matched = {
            _normalized_label(_text_content(item)): item
            for item in items
            if _normalized_label(_text_content(item)) in expected
        }
        if best is None or len(matched) > len(best[1]):
            best = (element, matched)
    return best if best and best[1] else None


@register.simple_tag
def render_homework_question_prompt(prompt, options, question_type):
    """Render choice controls inside the authored option list when possible.

    Authored prose, links, hints, and formatting remain as rendered. The
    original option list becomes the one interactive list. If an old prompt
    has no matching list, all answer controls are appended as the fallback.
    """
    prompt_text = prompt or ""
    prompt_html = str(prompt_text) if isinstance(prompt_text, SafeData) else str(escape(prompt_text))
    if question_type not in {"choice", "checkbox"} or not options:
        return mark_safe(prompt_html)

    parser = _FragmentParser(prompt_html)
    option_map = {
        _normalized_label(option.label): (option, selected)
        for option, selected in options
    }
    list_match = _matching_option_list(parser.roots, options)
    matched_labels = set()
    control_type = "checkbox" if question_type == "checkbox" else "radio"

    if list_match:
        target_list, matched_items = list_match
        target_list.start = _append_attribute(
            target_list.start, "data-homework-option-list", "true",
        )
        for label, item in matched_items.items():
            _replace_option_item(item, option_map[label], control_type)
            matched_labels.add(label)
        if question_type == "choice" and any(selected for _option, selected in options):
            target_list.children.append(_Raw(
                '<li class="homework-option-clear"><label class="homework-option-label">'
                + _input_html("radio", "", False)
                + '<div class="homework-option-content">Leave unanswered</div>'
                '</label></li>'
            ))
        missing = [
            option for label, option in option_map.items()
            if label not in matched_labels
        ]
        for option, selected in missing:
            target_list.children.append(_Raw(
                '<li data-homework-option="true"><label class="homework-option-label">'
                + _input_html(control_type, option.key, selected)
                + f'<div class="homework-option-content">{escape(option.label)}</div>'
                '</label></li>'
            ))
    else:
        controls = []
        for option, selected in options:
            controls.append(
                '<li data-homework-option="true"><label class="homework-option-label">'
                + _input_html(control_type, option.key, selected)
                + f'<div class="homework-option-content">{escape(option.label)}</div>'
                '</label></li>'
            )
        if question_type == "choice" and any(selected for _option, selected in options):
            controls.append(
                '<li class="homework-option-clear"><label class="homework-option-label">'
                + _input_html("radio", "", False)
                + '<div class="homework-option-content">Leave unanswered</div>'
                '</label></li>'
            )
        parser.roots.append(_Raw(
            '<ul class="homework-option-fallback" data-homework-option-list="true">'
            + "".join(controls)
            + "</ul>"
        ))

    return mark_safe("".join(_serialize(node) for node in parser.roots))
