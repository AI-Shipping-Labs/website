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

## LLM_MAX_RETRIES

Purpose: Maximum retry attempts passed to the Anthropic-compatible SDK client.
It also bounds the wrapper's retry behavior. The default is 6.

Without it: The client uses 6 retries. A negative value resolves to 0, which
disables retries; an invalid non-integer falls back to 6.

Where to find it: This is an operator reliability/cost choice rather than a
provider-issued value. Set it in Studio > Settings > LLM Provider or through
the authenticated integration-settings API. A Studio override applies to the
next client created without a redeploy.

Test vs live: Keep retries low in deterministic tests. In production, balance
transient-provider resilience against latency and duplicate request cost.

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

## ONBOARDING_AI_STREAMING

Default `true`. When false, onboarding uses the blocking POST transport.
Both transports retain the same request UUID, provider deadline, attempt
budget, atomic persistence, and telemetry contract.

## ONBOARDING_AI_DEADLINE_SECONDS

Default `25`; runtime values are clamped to 5-28 seconds. One absolute
monotonic deadline wraps the complete call or entire stream iteration; the
same ceiling is passed to the provider SDK. Per-read activity cannot extend
the logical turn beyond it. A cancellation token synchronously closes the
provider client at the deadline for both streaming and blocking calls, and the
bounded worker confirms cleanup before returning the timeout. The ceiling
stays below the default 30-second sync Gunicorn worker timeout. The browser
deadline is three seconds higher; an
abort restores the same UUID/message and never races an automatic blocking POST.

## ONBOARDING_AI_MAX_ATTEMPTS

Default `2`; runtime values are clamped to 1-3. This is the total outbound
provider-call budget for one logical request UUID, including a streaming
failure followed by blocking recovery. Onboarding passes `max_retries=0` to
the provider client so SDK retries cannot multiply this budget.

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

## Streaming (issue #806)

`integrations.services.llm` exposes a `stream(...)` generator alongside
`complete(...)` for token-by-token delivery:

```python
from integrations.services import llm

for event in llm.stream(messages, system=system):
    if event.kind == 'text_delta':
        write_to_client(event.text)       # incremental chunk
    elif event.is_done:
        result = event.result             # fully assembled LLMResult
```

Contract:

- `stream(messages, *, model=None, system=None, max_tokens=..., temperature=None)`
  yields `StreamEvent` objects: zero or more `text_delta` events (each
  carrying the next chunk on `.text`), then exactly one terminal `done`
  event whose `.result` is the SAME `LLMResult` `complete()` would return
  for the same input (`.text` is the concatenation of every delta;
  `.tool_input` is populated when a tool was used).
- Tools are supported on the streaming surface. The terminal event carries
  the same provider-neutral tool input and token-usage fields as `complete()`.
  Onboarding uses that terminal result for final extraction, preserving one
  provider generation on a successful streamed turn.
- Provider selection mirrors `complete()`: the backend is chosen from
  `LLM_PROVIDER` via the registry; an unimplemented provider raises
  `LLMError` ("provider X not supported yet") before any network call.
  Only the `anthropic` backend implements `stream()` (SDK
  `client.messages.stream(...)`); the client is built per call from
  `get_config()`, never cached at import. `openai`/`bedrock` are reserved
  seams — adding streaming there is registering a backend method, not
  editing callers.
- Error contract for the transport fallback: a transport/SDK failure while
  OPENING the stream (before any delta) raises `LLMError` eagerly, just
  like `complete()`. A mid-stream failure (after at least one delta) is
  re-raised from the generator (wrapped in `LLMError`) and is NOT retried
  from scratch — replaying tokens would corrupt the stream — so the caller
  can fall back to the non-streaming path for the same message. The API
  key never appears in any raised message (same scrub + test as
  `complete()`).

### Onboarding chat transport (SSE)

The member-facing onboarding chat (#806) streams the assistant reply to
the browser over Server-Sent Events.

Transport choice:

| Option | Fit | Verdict |
|--------|-----|---------|
| SSE via `StreamingHttpResponse` (`text/event-stream`) | Native browser support, one-directional server->client text, no client build step, plain HTTP/Django WSGI | Chosen |
| WebSocket (Channels/ASGI) | Requires an async stack the platform does not run (sync gunicorn WSGI, no build step) | Rejected (over-engineered for one-way push) |
| Chunked long-poll / fetch streaming of plain text | Works, but SSE gives a framed `event:`/`data:` protocol for free | SSE preferred |

The streaming endpoint is `POST /onboarding/chat/stream`. Because
`EventSource` cannot POST, the client uses `fetch` + a `ReadableStream`
reader and parses the SSE frames itself (vanilla JS in
`templates/accounts/onboarding_chat.html`, no build step). SSE event
kinds: `delta` (`{"text": ...}`), `done`
(`{"complete": bool, "redirect": url|null}`), and `fallback`
(`{"reason": ...}`). Typed `error` events keep the input usable and offer
the always-visible switch-to-form path.

Streaming is gated by `ONBOARDING_AI_STREAMING` (default on when the AI
path is on; Studio-configurable, switchable without a redeploy). When off
or when the LLM is disabled, the chat uses the non-streaming v1 transport
(`POST /onboarding/chat/message`) and opens no SSE connection.

Every member submission carries a browser-generated UUID in both streaming
and blocking POSTs. `OnboardingTurnAttempt` durably hashes the message and
admits at most one processing call per conversation. A successful duplicate
reloads the durable transcript without provider work; an in-flight or
different-tab request returns `busy` while preserving the submitted UUID and
message; reusing a UUID for different content is rejected. An exhausted failed
UUID receives a fresh UUID before another provider call. The provider call
runs outside a database transaction, then conversation/response rows are
locked and a version check atomically applies exactly one transcript pair and
any final answers.

Graceful degradation is mandatory: the persisted #800 `Response` /
`ResponseQuestion` / `Answer` artifacts are IDENTICAL whether the turn
streamed or not (the streaming view reuses the v1 `run_onboarding_turn`
decision logic and the v1 finalization). The server persists the turn only
AFTER the authoritative result is assembled, so a stream failure writes
nothing. Recovery re-issues the SAME message and request UUID through
`/onboarding/chat/message`, consuming the next call in the shared bounded
budget. Deadline/client-abort recovery does not auto-submit while the original
request may still commit. A hard provider failure routes the member to the
#802 form fallback.

Final member state commits independently of staff notification delivery. The
final attempt row is a durable outbox: pending enqueue failures, delivery
failures, and expired `processing` leases are retried by the
`onboarding-staff-notification-recovery` five-minute schedule. Its attempt
count, lease, and safe exception class never contain member content.

### Worker-model and proxy implications

- Gunicorn runs sync workers. A long-lived SSE response holds ONE sync
  worker for the full duration of the stream. Onboarding streams are short
  and low-concurrency, so sync workers are acceptable here. The enforced
  provider deadline is the worker-protection mechanism. A future move to
  `gevent`/async workers would relax the one-worker-per-stream cost.
- Buffering proxies: nginx and CloudFront may buffer `text/event-stream`,
  defeating incremental delivery. The response sets `Cache-Control:
  no-cache` and `X-Accel-Buffering: no` (nginx honours the latter) to
  discourage buffering. These headers do NOT guarantee CloudFront chunk
  pass-through. A correctly buffered response can arrive only at completion
  and cannot be detected as an error by the browser. Any nginx/CloudFront
  buffering config lives
  in the infra repo (`AI-Shipping-Labs/ai-shipping-labs-infra`); file a
  follow-up there if buffering is observed in production. It is NOT
  provisioned from this repo.

### Onboarding AI latency runbook

The durable attempt row and structured `onboarding_turn_terminal` records
contain only internal IDs, hashes, enums, numeric timings, call counts, model,
and token usage. Each logical request emits exactly one terminal record. A
recoverable streaming failure is retained on the durable row but is not logged
as terminal before its same-request fallback reaches the final outcome.
Fallback success preserves the first-stream error and TTFT; total wall time
aggregates monotonic elapsed intervals across bounded attempts. Never add
member messages, transcript, prompts, assistant
text, tool input/answers, persona content, email, credentials, or raw provider
exceptions to this record.

PostgreSQL p50/p95 over the last 24 hours:

```sql
SELECT
  percentile_cont(0.50) WITHIN GROUP (ORDER BY ttft_ms) AS p50_ttft_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS p95_ttft_ms,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY total_duration_ms) AS p50_total_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY total_duration_ms) AS p95_total_ms
FROM questionnaires_onboardingturnattempt
WHERE created_at >= now() - interval '24 hours' AND status = 'succeeded';
```

Rates, calls, and token cost proxies:

```sql
SELECT
  avg(timed_out::int) AS timeout_rate,
  avg(fallback_used::int) AS fallback_rate,
  avg(provider_call_count) FILTER (WHERE status = 'succeeded') AS calls_per_success,
  avg(input_tokens) AS avg_input_tokens,
  avg(output_tokens) AS avg_output_tokens,
  avg(cache_read_tokens) AS avg_cache_read_tokens,
  avg(cache_write_tokens) AS avg_cache_write_tokens,
  model
FROM questionnaires_onboardingturnattempt
WHERE created_at >= now() - interval '24 hours'
GROUP BY model;
```

Correlate an incident by attempt ID from the structured log to the durable row.
Rollback order is: set `ONBOARDING_AI_STREAMING=false` first for SSE/proxy
problems; set `ONBOARDING_AI_ENABLED=false` second to return all members to
the form. Absence or failure of Logfire does not affect persistence or the
member response.
