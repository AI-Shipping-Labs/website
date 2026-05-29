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

# Evals (issue #812)

The eval layer measures the QUALITY of the two assistants over a labeled
dataset and validates an LLM judge against human gold labels. It is
DISTINCT from a pass/fail build gate: evals answer "how good is the
assistant, and is config A better than B?", not "is this build correct?".

It builds ON TOP of the #809 harness above. The fixture-loading,
`runner.run_callable`, and `FileTraceSink` plumbing is reused unchanged;
only the judge pass, the alignment math, and the report aggregation are
new. The choice was to extend `run_ai` with `--eval` / `--align` flags
rather than add a new command, because the suite path is already exactly
the callable-plus-trace loop evals need.

## The five-step loop (buildcamp v2 `06-evaluation`)

1. Define "good" via a rubric -- one correctness dimension per assistant
   (see `judge.py`). Let failure categories emerge from labeling.
2. Build the dataset by equivalence partitioning (the `dataset/` subtrees).
3. Label manually for a gold standard (the label CSVs; a `[HUMAN]` step).
4. Build an LLM judge and align it to the labels (`--align`).
5. Run evals as experiments (`--eval`); compare `% good` across configs.

## Datasets

Eval scenarios live under per-assistant `dataset/` subtrees, separate from
the few #809 demo fixtures:

- `fixtures/onboarding/dataset/*.json` (~20 scenarios)
- `fixtures/feedback/dataset/*.json` (~17 scenarios)

Each fixture is the SAME shape `runner.build_onboarding_input` /
`build_feedback_input` already parse, PLUS a `meta` sidecar the dataset
loader reads and the callable adapter ignores:

```json
{
  "meta": {
    "id": "onb-persona-alex",
    "category": "persona-inference",
    "phrasing": "direct",
    "source": "designed",
    "expected": {"persona_signal": "alex"}
  },
  "transcript": [...],
  "member_message": "...",
  "persona_catalog": [...]
}
```

| meta field | meaning |
|------------|---------|
| `id` | stable scenario id; the join key to the label CSV |
| `category` | equivalence-partition group (e.g. `persona-inference`, `failure-injection`) |
| `phrasing` | `direct` / `vague` / `wrong-terminology` / `broad` / `adversarial` |
| `source` | `designed` (human-authored) vs `synthetic` (LLM-generated edge case) |
| `expected` | optional deterministic expectation (e.g. `persona_signal`, `required_fields`, `input_terms`, `no_signal_expected`) so a metric can be computed without the judge |

Both datasets deliberately include EXPECTED-FAIL scenarios (category
`failure-injection`: persona-name leak, invalid enum, missing field,
hallucinated theme) so the judge has negatives to catch.

## Gold labels + labeling guide

One CSV per assistant: `labels/onboarding_labels.csv`,
`labels/feedback_labels.csv`. Columns (buildcamp `labels.csv` shape):

| column | meaning |
|--------|---------|
| `id` | matches a dataset scenario `id` |
| `correctness_label` | `pass` / `fail` (empty = not yet labeled) |
| `failure_category` | short emergent tag (`persona-leak`, `hallucination`, `missing-field`, `wrong-scope`, ...); empty on a pass |
| `split` | `dev` (tune the judge) / `test` (final validation) |
| `notes` | free-form, human-only, never fed to the judge |

This issue ships the SCAFFOLD: every scenario `id` is pre-listed, `split`
is pre-assigned ~75% dev / ~25% test (stratified so both splits carry pass
AND fail), and a few example rows are pre-labeled (the obvious expected-
fail injections + a couple of clear passes). The full human labeling of
every scenario is a `[HUMAN]` step.

Labeling guide -- "label before you define":

- Use a BINARY label (`pass` / `fail`), not a 1-5 scale.
- Observe failures FIRST, then name the `failure_category`. Do not predefine
  categories; let them emerge.
- BOTH pass and fail examples are required in each split -- an all-pass
  dataset cannot validate a judge.
- `notes` is for humans only and is never sent to the judge.

## Running an eval

```
python manage.py run_ai onboarding --eval \
  --suite integrations/services/ai_eval/fixtures/onboarding/dataset --live
python manage.py run_ai feedback --eval \
  --suite integrations/services/ai_eval/fixtures/feedback/dataset --live
```

Drop `--live` to run mocked (the default -- the canned judge verdict makes
the whole flow run with no key/network; that is what CI uses). The run
writes one out-subdir per scenario (`output.json` + `trace.json`) plus a
top-level `eval_report.json`, and prints a table:

- `% good` (judge pass rate) overall and per `category`.
- Deterministic `expected`-based metrics: onboarding (`no_persona_leak`,
  `extraction_complete`, `correct_persona`), feedback
  (`theme_ranking_correct`, `no_hallucinated_themes`,
  `recommendations_actionable`, `next_sprint_signal_correct`).
- Callable cost + latency and judge cost + latency, tracked SEPARATELY.
- Run metadata (provider, model, dataset dir, timestamp, judge-prompt
  version) for experiment comparison across runs.

Token usage/cost is read defensively off the #799 `LLMResult` exactly like
`FileTraceSink`: the `LLMResult` exposes no usage today, so cost reports
"usage unavailable" rather than crashing; the moment a future `LLMResult`
carries usage it is captured automatically.

## Running the alignment step

```
python manage.py run_ai onboarding --align \
  --suite integrations/services/ai_eval/fixtures/onboarding/dataset \
  --labels integrations/services/ai_eval/labels/onboarding_labels.csv --live
```

This runs the judge over the LABELED scenarios and measures agreement
against the human gold labels, writing `alignment_report.json`:

- accuracy, precision, recall, and the confusion matrix (TP/FP/TN/FN), with
  `fail` as the POSITIVE class (we care about catching real failures).
- dev-set and held-out test-set metrics reported SEPARATELY, so tuning the
  judge prompt on dev does not leak into the reported test number.
- per-scenario disagreement rows (human label vs judge label vs judge
  reasoning) so you can inspect exactly where the judge diverges.

Reading judge-vs-human metrics: the benchmark is human-level AGREEMENT, not
perfection. A judge that is consistent (even if imperfect) still makes
relative `% good` comparisons across eval runs meaningful. Iterate the
judge prompt on the dev disagreements only; the test split is the honest
final number. Bump `JUDGE_PROMPT_VERSION` in `judge.py` whenever you change
the judge prompt so reports stay comparable.

## Constraints

- The judge runs the single `correctness` dimension per assistant. A
  multi-dimension "god evaluator" is a documented FUTURE option
  (buildcamp `06-evaluation/04`), not built here.
- Real-provider eval runs (`--live`) are HUMAN-TRIGGERED. The heavy
  real-provider eval is NOT a default-CI gate; CI only runs the mocked
  plumbing tests.
- The eval code does NOT import or initialize Logfire (#813 gates
  production observability and must not fire in tests or evals); a mocked
  test asserts no Logfire emission during an eval run.
