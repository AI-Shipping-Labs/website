"""Scripted stub LLM for the eval harness's default mock mode (#809).

Mock mode is the default so an accidental run -- and all of CI -- never
hits a provider or needs an API key. :func:`mock_complete` returns a fixed
``LLMResult`` whose ``tool_input`` is a schema-valid canned answer for the
chosen callable, derived from the request's tool ``input_schema`` so it
stays valid as the schemas evolve. :func:`patch_llm` installs the stub on
the ``integrations.services.llm`` module the callables import, restoring
the real functions on exit.

No network call is made in mock mode; the real backend is never invoked.
"""

import contextlib

from integrations.services import llm
from integrations.services.llm import LLMResult


def _canned_tool_input(tool):
    """Build a schema-valid canned tool input from the tool's input_schema.

    Produces a minimal value for each declared property so the callable's
    ``model_validate`` accepts it without depending on a specific schema
    revision. Enums use their first allowed value; required fields are all
    populated.
    """
    schema = (tool or {}).get('input_schema') or {}
    defs = schema.get('$defs') or schema.get('definitions') or {}
    props = schema.get('properties') or {}
    result = {}
    for name, spec in props.items():
        result[name] = _value_for(spec, defs)
    return result


def _resolve_ref(spec, defs):
    ref = spec.get('$ref')
    if not ref:
        return spec
    key = ref.split('/')[-1]
    return defs.get(key, {})


def _value_for(spec, defs):
    spec = _resolve_ref(spec, defs)
    # anyOf / oneOf (e.g. nullable fields): if null is allowed, prefer it
    # (always valid for an optional field, and avoids producing a bogus
    # value for a string-format branch such as date). Otherwise pick the
    # first concrete branch.
    for combinator in ('anyOf', 'oneOf'):
        if combinator in spec:
            branches = spec[combinator]
            if any(b.get('type') == 'null' for b in branches):
                return None
            return _value_for(branches[0], defs)
    if 'enum' in spec:
        return spec['enum'][0]
    json_type = spec.get('type')
    string_format = spec.get('format')
    if json_type == 'string':
        if string_format == 'date':
            return '2026-01-01'
        if string_format == 'date-time':
            return '2026-01-01T00:00:00Z'
        return 'mock'
    if json_type == 'integer':
        return spec.get('minimum', 1)
    if json_type == 'number':
        return spec.get('minimum', 1)
    if json_type == 'boolean':
        return False
    if json_type == 'array':
        return []
    if json_type == 'object':
        nested_props = spec.get('properties') or {}
        return {n: _value_for(s, defs) for n, s in nested_props.items()}
    if json_type == 'null':
        return None
    # Untyped / unknown: a string is the safest broadly-valid default.
    return 'mock'


def mock_complete(
    messages,
    *,
    model=None,
    system=None,
    max_tokens=None,
    temperature=None,
    tools=None,
    tool_choice=None,
    timeout_seconds=None,
    max_retries=None,
    cancellation=None,
):
    """Stub ``complete`` returning a fixed, schema-valid structured result.

    When a tool is supplied (both callables always supply one), the
    returned ``tool_input`` is a canned schema-valid value, mirroring a
    forced tool call. ``text`` carries a short fixed mock string.
    """
    tool = (tools or [None])[0]
    tool_input = _canned_tool_input(tool) if tool else None
    tool_name = tool.get('name') if tool else None
    return LLMResult(
        text='[mock] scripted response',
        tool_input=tool_input,
        tool_name=tool_name,
    )


@contextlib.contextmanager
def patch_llm():
    """Patch ``llm.complete`` / ``llm.is_enabled`` for a mock run.

    Inside the context the callables see ``is_enabled() == True`` and a
    scripted ``complete`` that makes no network call. The originals are
    restored on exit even if the body raises.
    """
    original_complete = llm.complete
    original_is_enabled = llm.is_enabled
    llm.complete = mock_complete
    llm.is_enabled = lambda: True
    try:
        yield
    finally:
        llm.complete = original_complete
        llm.is_enabled = original_is_enabled
