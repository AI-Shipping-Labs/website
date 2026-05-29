# Observability (Pydantic Logfire) integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `observability` group
(issue #813). Each section follows the same template — Purpose, Without
it, Where to find it, Test vs live.

Observability wires Pydantic Logfire (OpenTelemetry-based) into the
running app so that, in production only, we collect logs and traces for
Django requests, outbound HTTP, and the LLM calls. It reuses the existing
integration-config machinery end to end: values resolve through
`integrations.config.get_config` / `is_enabled` (DB row > Django settings
> env > default), so a Studio save takes effect without a redeploy,
consistent with every other integration.

## Production-only by design

Logfire initializes at app startup ONLY when ALL three of these hold:

1. The process is not running the Django test suite
   (`settings.TESTING` is `False`; the test suite sets
   `TESTING = 'test' in sys.argv`).
2. A non-empty `LOGFIRE_TOKEN` is configured.
3. `LOGFIRE_ENABLED` is `true`.

This three-part AND gate lives in
`integrations/services/observability.py::logfire_is_enabled()` and is the
single guard the startup initializer
(`integrations.apps.IntegrationsConfig.ready()`) routes through. When the
gate is closed there is no `logfire` import side effect, no network, and
no `logfire.configure()` call.

Why off by default: `LOGFIRE_ENABLED` defaults to `false` everywhere, so
Logfire stays silent in:

- the Django test suite (the `TESTING` clause closes the gate),
- the eval harness (`manage.py run_ai`, issue #809) and the live-judge
  pytest set (issue #811) — neither runs under `manage.py test`, so the
  load-bearing guard there is the explicit `LOGFIRE_ENABLED` opt-in, not
  `TESTING`,
- any local/dev run unless an operator deliberately turns it on.

The eval/test callables are NOT instrumented. They stay silent purely by
virtue of the gate; no Logfire calls are added inside `run_ai`, the judge
harness, or the AI callables.

## What is instrumented

When the gate is open, `init_logfire()` calls `logfire.configure(...)`
once and then enables the auto-instrumentors available in the installed
Logfire:

- `logfire.instrument_django()` — request traces and view timing.
- `logfire.instrument_httpx()` and `logfire.instrument_requests()` —
  outbound HTTP. The `anthropic` SDK uses `httpx`, so instrumenting
  `httpx` captures the LLM HTTP calls.
- `logfire.instrument_anthropic()` — the Anthropic SDK directly, when the
  installed Logfire exposes that instrumentor (the current version does;
  if a future version drops it, the `httpx` instrumentation still covers
  the LLM transport).

Each instrumentor is guarded independently: a missing optional helper or a
failing call is logged and swallowed, so app boot never crashes on a
misconfiguration. `ready()` runs once per process (each gunicorn worker
and the qcluster configure their own Logfire exporter), so there is no
double-`configure()` within a single process.

Out of scope here: collector/OTel endpoint configuration beyond Logfire's
hosted endpoint (a follow-up for `ai-shipping-labs-infra`), and custom
spans inside business logic — this is bootstrap + auto-instrumentation
only.

## LOGFIRE_ENABLED

Purpose: Explicit on switch for Logfire. Must be `true` (plus a token,
plus not running tests) before Logfire initializes.

Without it: Treated as off. Logfire never initializes — no traces, no
network calls. This is the intended default for local, dev, eval, and
judge runs.

Where to find it: This is an operator choice, not a vendor-issued value.
Set it to `true` in production (via `.env` or Studio > Settings >
Observability) once a token is in place.

Test vs live: Leave it off (or unset) in dev/test/eval so those runs stay
silent. Set it `true` only in the production process where you want traces
collected.

## LOGFIRE_TOKEN

Purpose: Logfire write token used by `logfire.configure(token=...)` to
authenticate the exporter against your Logfire project.

Without it: Logfire is fully off. The gate stays closed even if
`LOGFIRE_ENABLED` is `true`.

Where to find it: Create a project in Logfire and copy a write token from
the Logfire project settings. The operator supplies their own token — it
is never committed to the repo. Set it in `.env` or via Studio >
Settings > Observability.

Test vs live: This key is `is_secret=True`: Studio renders it masked and
the JSON export redacts it. Use a token for your production Logfire
project; do not enable it in dev/test (the gate keeps it off anyway when
`LOGFIRE_ENABLED` is not `true`).

## LOGFIRE_ENVIRONMENT

Purpose: Logfire environment tag passed to
`logfire.configure(environment=...)`, so production traces are separable
from any opt-in dev run.

Without it: Defaults to `production`.

Where to find it: This is an operator label, not a vendor-issued value.
Leave it as `production` in prod; set a distinct value (e.g. `staging` or
`dev`) on any non-production process where you deliberately enable
Logfire, so its traces land under a separate environment in the Logfire
UI.

Test vs live: Optional. The value only matters when the gate is open, so
it has no effect in tests/evals where Logfire never initializes.
