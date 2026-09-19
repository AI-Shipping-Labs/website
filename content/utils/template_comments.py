"""The one implementation of "Django template tokens are single-line only".

``django.template.base.tag_re`` is a single non-DOTALL alternation over the
three constructs Django lexes::

    ({%.*?%}|{{.*?}}|{#.*?#})

Because the flag is absent, ``.`` never crosses a newline: a construct whose
closing marker sits on a later line is not tokenized at all, and its raw source
text is emitted as visible output.  For ``{# #}`` that means a developer note
renders on the page (book-club regressions) or inside a transactional email
body.  For ``{% %}`` and ``{{ }}`` it means the tag or variable silently stops
working and shows up as literal braces.

This module is deliberately a runtime module, not a test helper, because two
callers need the identical rule:

* ``content/tests/test_template_comment_lint.py`` -- the ``core``-tagged file
  lint over ``templates/**/*.html`` and ``email_app/email_templates/*.md``.
* ``studio/views/email_templates.py`` -- operator-authored
  ``EmailTemplateOverride`` copy, which no file lint can ever see because it
  lives in the database and is compiled by the same ``django.template.Template``
  call in ``email_app/services/email_rendering.py``.

A single-line ``{# ... #}`` is correct and stays fully supported: this is a rule
about span, not a deprecation of the construct.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ``(opening marker, closing marker, human token form)`` for every construct in
#: Django's ``tag_re`` alternation, in the same order.  The openers differ in
#: their second character, so no two can ever start at the same offset and the
#: ordering here only documents the alternation -- it never breaks a tie.
TOKEN_FORMS: tuple[tuple[str, str, str], ...] = (
    ("{%", "%}", "{% %}"),
    ("{{", "}}", "{{ }}"),
    ("{#", "#}", "{# #}"),
)

#: The comment form, which gets its own remediation copy.
COMMENT_TOKEN = "{# #}"

#: What each construct is CALLED in operator-facing copy.  ``{{ }}`` is a
#: variable, not a tag: the Studio email-template editor's own Subject help text
#: says "Supports Django variables, e.g. {{ tier_name }}" on the same screen, so
#: calling it a tag here would contradict the field label in the operator's
#: field of view.
TOKEN_NOUNS: dict[str, str] = {
    "{% %}": "tag",
    "{{ }}": "variable",
    COMMENT_TOKEN: "comment",
}

KIND_MULTILINE = "multi-line"
KIND_UNCLOSED = "unclosed"

_COMMENT_REMEDY = (
    " comment must open and close on the same line — use "
    "{% comment %} ... {% endcomment %} for notes spanning more than one line. "
    "Django renders an unclosed comment to the recipient."
)

#: ``{% %}`` and ``{{ }}`` share one remedy -- there is no block form to move
#: them to -- but each is named for what it is.
_MARKER_REMEDY = (
    " {noun} must open and close on the same line — put the opening and closing "
    "markers on one line. Django renders an unclosed {noun} to the recipient as "
    "raw text."
)


@dataclass(frozen=True)
class Offender:
    """One construct Django would refuse to tokenize."""

    line: int
    kind: str  # KIND_MULTILINE or KIND_UNCLOSED
    token: str  # "{# #}", "{% %}" or "{{ }}"
    excerpt: str


def _one_line_excerpt(text: str, limit: int = 140) -> str:
    excerpt = " ".join(text.split())
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 1].rstrip() + "…"
    return excerpt


def find_token_offenders(source: str) -> tuple[Offender, ...]:
    """Return every multi-line or unclosed Django construct in ``source``.

    Detection rule (exact, no rendering, no HTML parsing, no masking):

    * Scan left to right for the EARLIEST of ``{%``, ``{{`` and ``{#``, exactly
      as Django's single alternation does.
    * Find the FIRST following closing marker for that opener (Django's
      non-greedy ``.*?`` closes at the first one).
    * No closing marker -> ``unclosed`` offender, and scanning stops: every
      later construct is inside the junk region and would only cascade.
    * The span from opener through closer contains a newline -> ``multi-line``
      offender.  Scanning then resumes one character past the opener, because
      Django does not consume a region it could not tokenize.
    * Otherwise the construct is well-formed; scanning resumes after its closer,
      so markers *inside* a valid token (``{# mentions {% a %} #}``) are not
      re-examined -- which is precisely what keeps this free of false positives.

    The reported line is the 1-based line of the opening marker.
    """
    offenders: list[Offender] = []
    index = 0
    length = len(source)
    while index < length:
        earliest: tuple[int, str, str, str] | None = None
        for opening, closing, token in TOKEN_FORMS:
            at = source.find(opening, index)
            if at != -1 and (earliest is None or at < earliest[0]):
                earliest = (at, opening, closing, token)
        if earliest is None:
            break
        open_at, opening, closing, token = earliest
        line = source.count("\n", 0, open_at) + 1
        close_at = source.find(closing, open_at + len(opening))
        if close_at == -1:
            offenders.append(
                Offender(
                    line=line,
                    kind=KIND_UNCLOSED,
                    token=token,
                    excerpt=_one_line_excerpt(source[open_at:]),
                )
            )
            break
        span = source[open_at : close_at + len(closing)]
        if "\n" in span:
            offenders.append(
                Offender(
                    line=line,
                    kind=KIND_MULTILINE,
                    token=token,
                    excerpt=_one_line_excerpt(span),
                )
            )
            index = open_at + 1
        else:
            index = close_at + len(closing)
    return tuple(offenders)


def describe_offender(offender: Offender) -> str:
    """Return operator-facing copy naming the line, the construct and the fix."""
    opening = f'Line {offender.line}: a "{offender.token}"'
    if offender.token == COMMENT_TOKEN:
        return opening + _COMMENT_REMEDY
    return opening + _MARKER_REMEDY.format(noun=TOKEN_NOUNS[offender.token])
