# Standalone AI-eval harness (issue #809)

A developer/operator CLI for driving the platform's two Django-independent
AI callables outside the web request cycle, capturing structured output
plus a full trace for review and diffing:

- `feedback` -> `integrations.services.feedback_synthesis.synthesize_feedback` (#805)
- `onboarding` -> `questionnaires.onboarding_ai.run_onboarding_turn` (#804)

The entry point is the management command `run_ai`. It boots Django
settings so the LLM config resolves from DB/env, but it does NOT start the
server, render templates, or require an HTTP request. There is no
member-facing surface; this is operator tooling only.

## Modes

- `--mock` (default): a scripted stub LLM returns a fixed, schema-valid
  structured result. No network call, no API key. All automated tests and
  CI use this mode.
- `--live`: use the real configured provider. Requires `llm.is_enabled()`;
  if the LLM is not configured the command exits non-zero with a clear
  "LLM not configured" message and makes no network call.
- `--mock` and `--live` are mutually exclusive.

`--live` reuses the EXISTING LLM config from #799 (`LLM_API_KEY`,
`LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`) resolved via Studio or env.
There is no separate provider setup for this tool.

## Single run

```
python manage.py run_ai feedback --input integrations/services/ai_eval/fixtures/feedback/sprint_basic.json
python manage.py run_ai onboarding --input integrations/services/ai_eval/fixtures/onboarding/mid_conversation.yaml
```

Add `--live` to hit the real provider, `--model <name>` to override the
model, and `--out <dir>` to choose the output directory.

Each run prints a short summary and writes two files into the out dir:

- `output.json` — the callable's parsed result (`model_dump`).
- `trace.json` — the captured trace (see format below).

## Suite run

```
python manage.py run_ai feedback --suite integrations/services/ai_eval/fixtures/feedback/
python manage.py run_ai onboarding --suite integrations/services/ai_eval/fixtures/onboarding/
```

Runs every `.json` / `.yaml` / `.yml` fixture in the directory, writes one
out-subdir per fixture (named after the fixture stem) with its own
`output.json` + `trace.json`, plus a top-level `summary.json`, and prints
a results table (fixture, status, latency).

## Output locations

By default everything lands under
`.tmp/ai_runs/<callable>/<UTC-timestamp>/` (single run) or
`.tmp/ai_runs/<callable>/suite-<UTC-timestamp>/` (suite). `.tmp/` is
gitignored, so runs never get committed. Override with `--out <dir>`.

## Trace format (`trace.json`)

| Field | Notes |
|-------|-------|
| `callable` | `feedback` or `onboarding` |
| `provider` | resolved `LLM_PROVIDER` |
| `model` | resolved `LLM_MODEL` / `--model` |
| `timestamp_utc` | run start, UTC |
| `system_prompt` | the versioned system prompt sent |
| `messages` | full message list sent to the provider |
| `tool` | tool spec including `input_schema` |
| `raw_result` | `text`, `tool_name`, `tool_input` from the `LLMResult` |
| `token_usage` | recorded when the `LLMResult` exposes usage; `null` otherwise |
| `latency_seconds` | wall-clock for the LLM call |
| `parsed_output` | the validated result, `model_dump` |
| `error` | type + safe message; present only on failure |

The API key never appears in stdout or in `trace.json`.

Note on `token_usage`: the #799 `LLMResult` does not currently carry token
usage, so this field is `null` today. If a future `LLMResult` grows a
read-only `usage` attribute, the sink captures it automatically with no
change here.

## Fixture formats

### Feedback

Maps directly onto `SprintFeedbackInput`:

```json
{
  "sprint_name": "Sprint 7",
  "start_date": "2026-04-06",
  "duration_weeks": 4,
  "response_count": 3,
  "responses": [
    {"answers": [["question text", "long_text", "answer text"], ...]}
  ]
}
```

`start_date`, `duration_weeks`, and `response_count` are optional. Each
entry in `answers` is a `[question_text, question_type, answer_text]`
triple.

### Onboarding

```yaml
transcript:
  - {role: assistant, content: "..."}
  - {role: user, content: "..."}
member_message: "latest member text"   # null opens the conversation
persona_catalog:
  - signal: alex
    archetype: "Experienced engineer new to AI"
    description: "..."
    questions:
      - {prompt: "...", question_type: long_text}
      - {prompt: "...", question_type: number, options: ["a", "b"]}
```

`member_message: null` with an empty `transcript` returns the
deterministic greeting without any LLM call. Each `persona_catalog` entry
maps onto `PersonaInfo`.

A malformed fixture produces a clear parse error naming the file/field and
a non-zero exit (no raw stack trace).
