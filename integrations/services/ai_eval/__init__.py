"""Standalone AI-eval harness (issue #809).

Drives the two Django-independent AI callables -- sprint-feedback
synthesis (#805) and onboarding-interview (#804) -- outside the web
request cycle, against either a stub LLM (the default, no network/key) or
the real configured provider (``--live``). Each run writes a structured
``output.json`` plus a captured ``trace.json`` for review/diffing.

This package is operator/developer tooling. It does NOT modify the two
callables' signatures or the #799 LLM service public surface; it only
wraps them via their existing ``TraceSink`` hooks. The management command
:mod:`integrations.management.commands.run_ai` is the entry point.
"""
