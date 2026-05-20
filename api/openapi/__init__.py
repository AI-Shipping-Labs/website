"""OpenAPI scaffolding for the JSON API (issue #722).

Public interface:

- ``openapi_spec`` -- decorator that attaches OpenAPI metadata to a view
  function. The metadata is read at build time by ``build_spec``; the
  decorator itself adds zero runtime cost (it returns the view unchanged
  apart from setting an attribute).
- ``OPENAPI_SPEC_ATTR`` -- canonical attribute name used to stash the
  metadata. ``"__openapi_spec__"`` keeps the dunder convention so it is
  obvious that the attribute is framework-private.
- ``build_spec`` -- walks the ``api/urls.py`` resolver, reads the
  decorator metadata off each view, and assembles an OpenAPI 3.1
  document via the ``apispec`` library.

The decoration goes BELOW the existing ``@token_required`` /
``@csrf_exempt`` / ``@require_methods`` chain so the wrapped function
still carries that chain intact. ``openapi_spec`` reaches through
``functools.wraps`` (which sets ``__wrapped__``) to the innermost view
so the attribute lives on the function definition itself, not on a
transient wrapper.
"""

from api.openapi.builder import build_spec
from api.openapi.decorator import OPENAPI_SPEC_ATTR, openapi_spec

__all__ = ["OPENAPI_SPEC_ATTR", "build_spec", "openapi_spec"]
