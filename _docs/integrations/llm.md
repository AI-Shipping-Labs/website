# LLM Provider integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `llm` group (issue #799).
Each section follows the same template — Purpose, Without it, Where to
find it, Test vs live.

The platform exposes a provider-neutral LLM capability behind
`integrations.services.llm`. Callers depend only on that wrapper
(`is_enabled()`, `complete(...)`, `LLMResult`, `LLMError`) — never on a
specific vendor SDK. The wrapper selects a backend from `LLM_PROVIDER`
and resolves credentials/model/base-URL from `get_config()` at call time,
so a Studio override takes effect without a redeploy and worker processes
read fresh values.

## Providers

- `anthropic` is implemented. It uses the official `anthropic` Python SDK
  pointed at `LLM_BASE_URL`, so the same backend talks to Anthropic
  directly or to any Anthropic-compatible gateway (for example a
  Z.ai-style endpoint) by overriding the base URL.
- `openai` and `bedrock` are reserved names with no backend yet. Setting
  `LLM_PROVIDER` to either disables the feature (`is_enabled()` returns
  `False`) and any `complete(...)` call raises `LLMError` naming the
  provider, before any network call is made. A future issue adds those
  backends by registering a new backend object — callers do not change.

## Example: Anthropic-compatible gateway (Z.ai)

To run against an Anthropic-compatible gateway such as Z.ai, keep
`LLM_PROVIDER=anthropic` (the same backend speaks the Anthropic Messages
API) and override only the base URL, model, and key:

```
LLM_PROVIDER=anthropic
LLM_BASE_URL=https://api.z.ai/api/anthropic
LLM_MODEL=glm-5.1
LLM_API_KEY=<your Z.ai key>
```

Set these in `.env`, or enter them under Studio > Settings > AI (the
Studio value overrides `.env`). `LLM_PROVIDER` stays `anthropic` because
the gateway is Anthropic-compatible — you are not selecting a different
backend, only redirecting the same one. Use your own Z.ai key (never
committed to the repo), consistent with the `LLM_API_KEY` section below.

## LLM_PROVIDER

Purpose: Selects which backend the LLM service uses. Defaults to
`anthropic`.

Without it: Falls back to the `anthropic` default, so the Anthropic
backend is used.

Where to find it: This is an operator choice, not a vendor-issued value.
Leave it as `anthropic` (covers Anthropic and Anthropic-compatible
gateways via `LLM_BASE_URL`). `openai` and `bedrock` are reserved.

Test vs live: The same provider value works in dev and prod — the
difference is the key and (optionally) the base URL.

## LLM_API_KEY

Purpose: API key/credential for the selected provider. For `anthropic`
this is an Anthropic API key (or a key issued by an Anthropic-compatible
gateway such as Z.ai).

Without it: LLM features are disabled. `is_enabled()` returns `False` and
`complete(...)` raises `LLMError` without making any network call.

Where to find it: For Anthropic, create a key in the Anthropic Console
under API Keys. For a compatible gateway, use the key that gateway
issues. The operator supplies their own key — it is never committed to
the repo. Set it in `.env` or via Studio > Settings > AI.

Rotation: Paste a new key into Studio (or update `.env` and redeploy).
The Studio DB override wins over the env value, so a Studio save takes
effect on the next call without a restart.

Test vs live: Use a separate key (and ideally a separate account or
gateway project) for dev/staging so test traffic does not draw down the
production budget. This key is `is_secret=True`: Studio renders it masked
and the JSON export redacts it; it never appears in log lines or
exception messages.

## LLM_BASE_URL

Purpose: Base URL of the provider API. Defaults to
`https://api.anthropic.com`.

Without it: Falls back to the default Anthropic endpoint.

Where to find it: Leave the default for Anthropic. To use an
Anthropic-compatible gateway/proxy, set this to the gateway's
Anthropic-compatible base URL (for example a Z.ai-style endpoint). The
gateway's docs state the exact URL.

Test vs live: Point dev/staging at a sandbox gateway or the same
Anthropic endpoint with a test key; point prod at the production
endpoint/gateway.

## LLM_MODEL

Purpose: Default model name used when a caller does not pass an explicit
model. Defaults to `claude-sonnet-4-5`.

Without it: Falls back to `claude-sonnet-4-5`.

Where to find it: Use a model name supported by the selected provider or
gateway. Callers can still override per call by passing `model=...` to
`complete(...)`.

Test vs live: Pin a cheaper/faster model in dev if desired; the
production value is whatever quality/cost trade-off the feature needs.

## ONBOARDING_AI_ENABLED

Purpose: Toggles the conversational AI onboarding flow (issue #804) at
`/onboarding/`. When on (and the LLM is enabled), new members are offered
a chat interviewer; when off, `/onboarding/` shows the form-first flow
only. Defaults on.

Without it: Treated as on whenever the LLM service is enabled. The flag
exists to turn the AI path off without disabling the whole LLM service,
and is switchable from Studio without a redeploy.

Where to find it: This is an internal feature flag, not a provider
credential. Set it to `false` to force the form-first onboarding path
even when the LLM is configured.

Test vs live: The automated tests mock the LLM at the boundary, so this
flag is exercised both on and off without a live call.

## Structured output

`complete(...)` works for plain text and for structured output. For
structured output, callers build a tool spec from a Pydantic model and
let the model fill it in:

```python
from pydantic import BaseModel
from integrations.services.llm import complete

class Verdict(BaseModel):
    verdict: str

tool = {
    'name': 'verdict',
    'description': 'Structured verdict',
    'input_schema': Verdict.model_json_schema(),
}

result = complete(
    [{'role': 'user', 'content': 'Classify this...'}],
    tools=[tool],
    tool_choice={'type': 'tool', 'name': tool['name']},
)

parsed = Verdict.model_validate(result.tool_input)
```

The Anthropic backend forwards `tools` and `tool_choice` to the Messages
API, and the returned `LLMResult` exposes the tool's input dict on
`result.tool_input` so the caller validates it against the schema. The
wrapper itself does not import Pydantic — callers own their schemas. This
is the pattern the onboarding assistant (#804) and feedback synthesis
(#805) build on.

## Notes

- Retries: the Anthropic client is constructed with a configurable
  `max_retries` (default 6, tunable via the `LLM_MAX_RETRIES` env var,
  which is intentionally not surfaced in the Studio settings GUI). On top
  of the SDK's own retries, the wrapper catches the transient SDK
  exception types (`RateLimitError`, `APIConnectionError`,
  `APITimeoutError`, `InternalServerError`) and applies a bounded
  exponential backoff with jitter, then surfaces the failure as
  `LLMError`. Non-transient errors (auth, bad request) are not retried —
  they convert to `LLMError` immediately.
- Token safety: the API key is never included in any exception message or
  log line emitted by the service (asserted in tests).
- Client construction: the Anthropic client is built per call from
  `get_config()`, never cached at import, so Studio overrides and worker
  processes always read the current key/base-URL/model.
