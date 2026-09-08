# Website code audit and complexity-reduction plan

Date: 2026-09-07. Baseline commit: `1343497b0003093d4a1430e9784118210e36acfa`.
Review or retire this point-in-time plan by 2027-03-07.

## Recommendation

The audit records 57 findings: 16 content/frontend, 15 identity/messaging, 15
operations/integrations and 11 project/tooling findings. They include confirmed
defects, timing-dependent risks and opportunities to remove duplicate code. The
catalog distinguishes those categories; they are not 57 proven production incidents.

Start with these boundaries before broad refactoring:

| Finding | Why it comes first |
|---|---|
| [WEB-01](2026-09-07-website-code-audit-findings.md#web-01), project HTML | Member-submitted active HTML reaches staff review before approval |
| [WEB-02](2026-09-07-website-code-audit-findings.md#web-02), notification bell | Comment-derived text reaches an unsafe browser HTML sink |
| [OPS-01](2026-09-07-website-code-audit-findings.md#ops-01), Maven claims | Current nullable-join locking query fails on PostgreSQL; existing issue #1574 |
| [WEB-04](2026-09-07-website-code-audit-findings.md#web-04), [WEB-05](2026-09-07-website-code-audit-findings.md#web-05), comment access | Direct APIs bypass parent access or note privacy decisions |
| [IAM-06](2026-09-07-website-code-audit-findings.md#iam-06), progress apply/undo | Stale automated work can overwrite and later undo human completion |
| [SYS-01](2026-09-07-website-code-audit-findings.md#sys-01), CLI transport | A foreign absolute URL receives the configured staff credential |

Keep the Django modular monolith. Reduce the number of independent implementations
of the same rule: content access, safe rendering, operation validation, delivery state,
API serialization and browser request handling. Fix the correctness defects at those
boundaries first, then make every caller use the corrected owner and delete the old code.

The largest potential source reduction is in the paired API/Studio workflows and the
test suite. Splitting long functions into more files will make some code easier to read,
but will not by itself reduce the number of concepts, branches or lines maintained.
Do not begin with a framework migration, a microservice split, or Django app renaming.

This is an audit and implementation plan, not an implementation. The application was
not changed, production was not accessed, and no deployment was performed. The two
pre-existing CLI edits were preserved. The detailed catalog contains source evidence,
failure triggers, remediation steps and per-finding checks:

- [Finding catalog](2026-09-07-website-code-audit-findings.md).
- [Machine-readable baseline](2026-09-07-website-code-audit-metrics.json).
- [Earlier audit](2026-08-30-adversarial-code-audit.md), useful for context but not a substitute for rechecking current source.

## Scope and confidence

Three independent `gpt-5.6-sol` subagents at high reasoning reviewed bounded groups of
application domains. The coordinating review measured the whole tracked source tree,
reviewed test/build/project machinery and CLI boundaries, consolidated overlaps,
checked existing issue states and challenged high-impact claims.

The review covered every first-party application area through entry points, models,
services and representative tests. It did not manually inspect every line of the roughly
800,000-line tracked text corpus. High-risk flows received deeper tracing than routine CRUD.
Coverage means the area was examined; it is not a claim that the area is bug-free.

| Evidence label | Meaning | Required before closing an implementation issue |
|---|---|---|
| Reproduced | A small local/offline experiment demonstrated the stated behavior | Preserve the experiment as a regression at the appropriate layer |
| Confirmed from code | Current control flow establishes the condition; no live occurrence is claimed | Add a deterministic failing scenario and prove the fix |
| Risk / hypothesis | Impact depends on timing, configuration, policy or load not exercised here | Reproduce or measure before committing to the proposed design |
| Maintenance | Duplication, coupling or unnecessary work is visible | Demonstrate lower ownership/branch/line burden with behavior preserved |

Security findings describe prerequisites and affected surfaces in the catalog. No finding
asserts that production was compromised. Provider contracts and production configuration
were not tested. External AWS infrastructure and source content repositories were not
audited; they are separately owned repositories. Vendored Mermaid was counted separately,
not reviewed as first-party code or scanned for dependency vulnerabilities.

Line references identify the inspected snapshot and may move. The source scan used tracked
working-tree files; the two existing CLI edits were included in its small CLI/test buckets.
GitHub issue states were read on the audit date and are point-in-time observations.

## Measured baseline

Counts below are physical lines including comments and blank lines. They are neither
executable-statement counts nor a quality score. Python test definitions are AST declarations,
not collected test counts: inheritance, parametrization and markers change actual collection.

| Area | Files | Physical lines |
|---|---:|---:|
| Application Python, including management commands and project entry points | 811 | 172,467 |
| CLI Python | 26 | 3,052 |
| Operational Python under `scripts/` and `deploy/` | 27 | 15,456 |
| Migrations | 323 | 14,070 |
| Non-Playwright test Python, fixtures and test-policy support | 1,000 | 341,617 |
| Playwright Python, including fixture/support modules | 389 | 140,912 |
| HTML templates | 392 | 42,163 |
| First-party standalone JavaScript, including Tailwind config | 15 | 4,356 |
| Vendored JavaScript | 44 | 115,949 |
| CSS source | 2 | 340 |

Inline JavaScript belongs to the HTML count above. Generated Tailwind output is gitignored
and excluded. The first-party product baseline used for reduction targets is application
Python + HTML + first-party standalone JavaScript: `218,986` lines. The separate test-Python
baseline is `482,529` lines, about 2.80 times application Python. That ratio is a reason
to examine test value and maintenance cost; it does not establish that tests are redundant.

| Structural measure | Result | Interpretation |
|---|---:|---|
| Application functions | 4,631 | Nested function definitions included |
| Application functions over 100 lines | 142 | Review responsibility and branching, not just length |
| Application functions above Ruff McCabe 10 | 193 | 10 exceed complexity 25 |
| Ruff advisory diagnostics over the whole tree | 1,063 | 227 `C901`; other warnings include tests/tooling |
| Broad exception handlers in application Python | 221 | Some are intentional external-service boundaries |
| Exact AST-body duplicate candidate groups, functions at least 10 lines | 16 | Ignores docstrings, signatures and decorators; not automatic merge eligibility |
| Inter-area static import edges | 179 | Includes lazy imports; excludes relative/dynamic imports |
| Largest strongly connected import component | 20 areas | All measured application areas except `member_api`; not proof of a runtime circular-import failure |
| Test files with issue-number suffix patterns | 341 | A search aid for behavior-owner consolidation, not a deletion list |
| Selected policy/ratchet/scanner/manifest files | 24 / 20,288 lines | Overlaps test/tooling totals; do not add it again |

The import graph is coarse: `accounts` importing a content access helper and `content`
importing the user model creates a cycle at app granularity even if runtime imports work.
The actionable targets are domain code importing presentation helpers and repeated policy
implementations, not making this coarse graph acyclic by moving every model.

### Highest-leverage areas

| Area | Application Python lines | Test Python lines | Initial focus |
|---|---:|---:|---|
| Staff API | 31,088 | 36,219 | Input validation, serializers and domain operations shared with Studio |
| Studio | 27,215 | 64,543 | Thin HTTP adapters, shared forms/presenters and behavior-owned tests |
| Integrations | 20,178 | 32,996 | Content sync ownership, external-job lifecycle and provider boundaries |
| Content | 18,112 | 51,466 | One access/renderer policy, catalog queries and reusable templates |
| Accounts | 12,092 | 23,204 | Identity, merge, lifecycle and side-effect ordering |
| Events | 11,087 | 25,288 | Registration state, external meetings, reschedule/cancel delivery |
| Payments | 9,296 | 12,747 | Webhook claims, entitlement transitions and canonical test fixtures |
| Plans | 9,017 | 13,967 | Owned commands, concurrency checks and one browser state model |

Several long functions combine parsing, policy, persistence and side effects. Examples
include `_collect_event_values` (`api/views/events.py:473`, McCabe 47),
`_collect_series_values` (`api/views/event_series.py:193`, 40),
`_create_plan_from_payload` (`api/views/plans.py:434`, 36),
`_dispatch_events` (`integrations/services/github_sync/dispatchers/events.py:377`, 373 lines),
`event_detail` (`events/views/pages.py:570`, 369 lines), and
`handle_checkout_completed` (`payments/services/webhook_handlers.py:581`, 346 lines).
These are work-order signals. Preserve transaction and authorization invariants when
extracting helpers; do not scatter one atomic transition across unrelated modules.

## Intended ownership model

| Concern | Single owner | Callers | Code to retire after parity |
|---|---|---|---|
| Identity and effective access | Account/tier domain services plus content-parent resolvers | HTML views, staff/member API, background jobs | Inline tier checks and permissive unknown-content fallbacks |
| Content-to-HTML conversion | Existing canonical renderer/sanitizer with explicit trusted/untrusted policies | Models, sync, previews, public/Studio pages | Parallel raw Markdown renderers and unsafe cached HTML paths |
| Business writes | Small operations in the domain that owns the records | Studio forms, staff API, member API, jobs | Copied validation and mutation blocks in adapters |
| Read projections | Domain query/presentation functions | Studio pages, API serializers, CRM exports | Endpoint-private helpers imported across interfaces |
| Delivery | Existing durable delivery records with domain-specific identities | Email, reminders, notifications and external jobs | Independent check-send-log sequences and incompatible status names |
| Browser requests | One small transport boundary and feature-owned state transitions | Plan boards, editors, reusable widgets | Copied fetch/CSRF/error/retry plumbing and dormant fallbacks |
| Test ownership | One authoritative owner per distinct behavior at the cheapest layer that can prove it | CI and affected-tests mapping | Issue-specific replicas and source-string assertions |

Keep these as concrete functions or small types. There is no need for a universal
repository layer, generic CRUD engine, domain event bus, or homegrown workflow language.
Use an existing service when it already owns the invariant. Share mechanics only when
the callers have the same semantics; two similarly named functions can enforce different
privacy, consent, access or historical-compatibility policies.

## Reduction targets and measurement rules

Confirm a deletion inventory during the first work batch: exact legacy functions,
callers and tests, a non-overlapping estimate, and the retained behavior replacing each.
Set any program-wide forecast only after three representative packets report actual
net deletion and migration cost. No issue or acceptance gate should enforce a LOC quota.

| Measure | First milestone | Later target after evidence-based consolidation |
|---|---|---|
| First-party product lines | Establish net deletions in 3 representative vertical slices | Forecast the remaining reviewed inventory from observed results; count deletion as an outcome |
| Test/support Python lines | Remove one fully mapped batch of weak/duplicate assertions | Repeat where distinct behavior coverage is demonstrably retained; no repository-wide deletion quota |
| Independent owners per business rule | Inventory selected rules and adapters | One authority for every migrated rule; all selected old paths removed |
| Long/complex application functions | Use the 142 long functions and Ruff output to locate responsibilities | Count duplicate validators/branches/states removed; redistributing a function alone earns no simplification credit |
| API/Studio domain-policy copies | Choose three high-duplication workflows | Zero parallel copies in the selected workflows |
| Slow list-query behavior | Establish fixed fixtures at 1, 10 and 50 rows | Bounded query growth and SQL pagination for each migrated list |
| CI duration | Collect existing gate/shard timings | Demonstrable improvement without adding retries or weakening gates |
| Runtime image/context size | Measure a clean and scratch-containing build | No scratch/test artifact inclusion; record actual byte reduction |

Track added, deleted, moved and generated lines separately. Vendored/minified code,
historical migrations and generated OpenAPI should not be counted as product-code wins.
Do not remove helpful comments, rename files or move code into another repository to
claim reduction. Bug fixes may add code; assess the later consolidation batch as a whole.
Savings estimates in individual findings overlap and must not be summed.

The audit does not establish a repository-wide quantity of redundant code. It identifies
specific candidates and how to establish deletion eligibility. The first milestone must
produce a reviewed inventory, not a bulk removal. Preserve distinct tests for domain state,
view authorization, provider ambiguity, PostgreSQL races and browser wiring even when they
support the same feature. One cheap unit test cannot substitute for all those boundaries.

## Delivery and verification contract

Each work packet below should become a groomed issue or attach to an existing matching
issue. This audit did not create issues or modify existing issue bodies. Do not implement
the entire plan as one change or use the older roll-up tracker as an implementation ticket.

Follow `_docs/PROCESS.md`: PM grooming, engineering, tester verification, PM acceptance,
commit, local merge, push and on-call observation. Run at most three role subagents by
default. Parallelize independent domains; serialize edits to a shared service, access
resolver, renderer or test harness. Keep schema/queue compatibility changes separate
from large UI/test reorganization.

### Before implementation

- [ ] Reopen the referenced files at the current commit and reproduce the reported behavior.
- [ ] Check matching issue state and scope; reuse existing work rather than create a duplicate.
- [ ] List every caller, affected route, worker and persisted representation.
- [ ] Define the public contract, authorization matrix and invariant that must survive the change.
- [ ] Assign a domain owner and list exact legacy functions/files expected to disappear.
- [ ] Decide whether existing rows need repair; take counts through an authenticated API when production evidence is needed.
- [ ] Record rollback behavior, including compatibility with old workers and queued payloads.

### During implementation

- [ ] Add or adapt the authoritative regression before changing the broken behavior.
- [ ] Fix correctness in the existing path first when necessary to avoid a broad risky rewrite.
- [ ] Introduce the smallest shared owner and migrate one caller at a time.
- [ ] Compare old/new valid inputs and error responses; keep intentional API/HTML differences in adapters.
- [ ] Delete old code after its callers are gone, including test patch targets, task string paths and dynamic registrations.
- [ ] Update OpenAPI, operational docs and `IntegrationSetting` registrations where their contracts change.
- [ ] Record net code, branch/owner count, query and timing changes against the baseline.

### Local and CI checks

The source diff determines the local test scope. Do not manually widen it to the whole
Django suite. Print the selection, then run the exact commands it emits:

```bash
uv run python scripts/affected_tests.py --json
make test-affected
```

`make test-affected` is the required final gate for implementation/review work. Use focused
tests during editing. If the map is wrong, fix `scripts/affected_tests.py` and its tests;
do not hide a mapping gap with an arbitrary broader run. Shared access, website, payment,
template and Playwright-fixture changes may select full Playwright under the authoritative
escalation table. Full Django and aggregate coverage remain CI gates, unless explicitly
requested locally. The audit itself did not run these suites.

| Behavior | Appropriate proof |
|---|---|
| Parsing, state transition and serializer contract | Focused unit/Django service/view test with concrete output and side-effect assertions |
| Unauthorized read/write | Actor/owner/tier matrix plus assertions that forbidden writes and deliveries did not occur |
| Concurrent duplicate work | Barrier-controlled competing transactions on PostgreSQL or the repository's relevant concurrency harness |
| External acceptance followed by crash | Fake provider accepts; persist/ack fails; retry reaches the explicit ambiguous-state policy |
| Browser JavaScript/XSS/retry behavior | Playwright using the actual template and JS, not HTML string matching |
| List performance | Fixed-size fixtures with query budgets and 1/10/50-row growth checks |
| Migration/backfill | Representative old/new rows, repeat execution, interruption/resume and mixed-version behavior |
| Deleted code | Caller/route/task/template inventory plus retained regressions; search absence alone is insufficient |
| Test removal | Boundary-specific ownership ledger and targeted temporary breakage of the exact behavior, reverted before commit |
| Runtime configuration | DB override, environment fallback, default, invalid values and worker cache refresh |
| Deployment simplification | Disposable image/config validation and on-call readiness/rollback evidence |

Test-deletion packets must report collected test IDs/counts, affected-test mapping changes,
marker/lane changes, coverage deltas and retained boundary coverage. Retired IDs are removed
only from live manifests; immutable original ceilings and reviewed golden digests never
shrink or grow during cleanup. A removed non-requirement needs explicit PM acceptance and
must not erase an existing release, privacy, authorization, data-loss or compatibility invariant.
Targeted temporary breakage proves the retained assertion can fail; it adds no mutation-testing
framework, dependency, campaign, manifest, threshold or new CI job. The repository already
evaluated mutation testing; this plan does not re-propose that pilot.

For delivery races, a unique database row created after an external call cannot undo an
already accepted send. Define `pending`, `claimed`, `succeeded`, `failed` and, where needed,
`unknown` outcomes explicitly. A lease expiring after an ambiguous send must not automatically
cause a resend. Reuse the fixed campaign machinery where its contract fits, and preserve
separate consent, suppression, recipient and per-domain identity rules.

For queue races, first check the broker: an ORM queue write in the same database
transaction can roll back with domain state. `transaction.on_commit` improves ordering
for many effects but does not alone prevent a crash between commit and enqueue. Use an
existing durable dispatch record/outbox only where the demonstrated failure requires it.

### Acceptance and rollout

- [ ] Tester reports the exact selected commands, counts and Playwright subset, with no unexplained failures.
- [ ] Changed pages receive rendered/browser review and screenshots according to the repository process.
- [ ] PM checks the user/operator outcome, error copy, empty states and API parity.
- [ ] Schema constraints are added only after duplicate/invalid rows have been identified and repaired safely.
- [ ] Backfills are bounded, restartable and observable; do not silently delete ambiguous historical records.
- [ ] Deploy through the existing process; on-call monitors the required pipeline and relevant error/delivery metrics.
- [ ] Remove temporary dual paths after the agreed observation/rollback window, not in an unrelated later cleanup.
- [ ] Update the audit ledger with issue, commit, tests, measured reduction and any remaining limitation.

## Work packets, dependencies and stop conditions

Effort below is an order-of-magnitude estimate for engineering and focused verification,
not an elapsed schedule. Packets overlap; do not add their ranges as a promised total.
The first corrective wave can proceed in independent tracks, followed by consolidation
in vertical slices. Every listed finding has detailed evidence and regression scenarios
in the catalog; the packet defines the shared implementation boundary.

| Packet | Scope and findings | Dependency / size | Completion evidence |
|---|---|---|---|
| A — Stop immediate access and rendering failures | `WEB-01`, `WEB-02`, `WEB-03`, `WEB-04`, `WEB-05`, `OPS-01`, `SYS-01` | Independent bounded fixes, then shared renderer migration; roughly 1–6 days per boundary | PostgreSQL Maven path succeeds; unauthorized content is denied; untrusted content is inert in real browser sinks; CLI credentials remain on the configured origin |
| B — Serialize identity and progress changes | `IAM-05`, `IAM-06`, `IAM-09`, `OPS-09`; reuse #1519 | Can run alongside A with separate ownership; 3–8 days in slices | Contending requests converge; human edits survive automated apply/undo; merge relations remain on one canonical account |
| C — Agree the email identity and consent contracts | `IAM-03`, `IAM-04` | Verified-email policy needs PM decision; scanner-safe unsubscribe is independently fixable; 2–6 days | Billing edits cannot silently violate the agreed identity contract; scanner GET/HEAD causes no preference write; signed one-click POST still works |
| D — Own event/webhook execution and attempts | `IAM-07`, `IAM-08`, `OPS-03`; reuse #1525 and #1266 | Extend current ledgers; R2 work is conditional on its existing release gate; 5–10 days | One live event execution; unique attempt allocation; one active Maven occurrence after the approved compatibility transition |
| E — Make delivery recovery explicit | `IAM-01`, `IAM-10`, `IAM-11`, `IAM-12`, `OPS-06`, `OPS-07` | Define shared semantics before new migrations; 2–3 weeks in caller-sized slices | Each external channel has a durable business identity and honest outcome; retry cannot duplicate successful bells or blindly resend unknown email |
| F — Make external jobs converge | `IAM-02`, `IAM-13`, `OPS-02`, `OPS-05`, `OPS-12` | Reuse D/E where relevant; 1–2 weeks in separate provider slices | Old membership jobs cannot reverse current access; sync requests survive handoff; stale uploads/provisioners cannot overwrite current ownership; unmatched webhooks have a durable disposition |
| G — Unify event writes | `OPS-04`, `OPS-11` and event input/series resolver duplicates | Fix immediate races first; coordinate with F; 2–3 weeks | API and Studio call one validated transition command; provider calls stay outside database locks; stale clients receive an explicit conflict |
| H — Unify plan writes and browser state | `OPS-08`, `OPS-10`, `WEB-13`, `WEB-14`; reuse #1528 and #1544 | Preserve both public API contracts initially; 2–3 weeks | One command per plan aggregate mutation; adapters retain their auth/envelope contracts; lost responses and retries recover from authoritative server state |
| I — Remove repeated queries, views and transports | `IAM-14`, `IAM-15`, `OPS-13`, `OPS-14`, `OPS-15`, `WEB-09`, `WEB-10`, `WEB-11`, `WEB-12`; reuse #1524 and #1534–#1545 as appropriate | Small deletions can start early; broader list rewrites follow access fixes; 1–2 weeks in slices | One transport/formatter/setting writer; bounded list queries; obsolete code has no supported caller; old Calendly counters disappear only after usage review |
| J — Shrink test maintenance | `SYS-06`, `SYS-07`; per-domain test consolidation from G–I | Ownership ledger before deletion; begin alongside one completed vertical slice | Retained tests catch representative failures; immutable ceilings unchanged; net code/collection/gate cost measured |
| K — Simplify build and development inputs | `SYS-02`, `SYS-03`, `SYS-04`, `SYS-05`, `SYS-11` | Independent of product schemas; 2–5 days total in small changes | Safe encoded CLI paths, useful tables, explicit direct dependencies, no scratch in images, no migration authoring on startup |
| L — Measure before changing project machinery | `SYS-08`, `SYS-09`, `SYS-10` | Timing/use/compatibility evidence required; 2–4 days investigation before implementation | Sharding improves actual maximum gate time; any retired path has supported-deploy evidence; complexity reporting establishes whether a later gate is warranted |
| M — Repair small product invariants | `WEB-06`, `WEB-07`, `WEB-08`, `WEB-15`, `WEB-16` | Independent of broad consolidation; 1–3 days per finding | Cancelled books reject writes, concurrent votes respect limits, analytics cannot fail the page, invalid chapter times cannot silently save, and widget sign-in returns to the originating content |

### First actionable batch

1. Finish [#1574](https://github.com/AI-Shipping-Labs/website/issues/1574) (`OPS-01`) as a small PostgreSQL-specific correctness fix. Do not bundle it with Maven schema redesign.
2. Groom the unsafe rendering and comment-access boundaries from the `WEB` catalog into independently reviewable fixes; use the existing sanitizer and parent access resolvers.
3. Fix the CLI origin boundary (`SYS-01`) in coordination with the existing local CLI work.
4. Select one bounded data-integrity bug (`IAM-06` or the shared signup race) as the next available independent track.
5. In parallel with implementation review, define the E packet's delivery-state and ambiguity contract. Migrate one transactional caller before introducing it everywhere.

Packet M's small fixes may fill an available independent track before the larger
consolidation packets. Packet letters group ownership; they are not a strict execution order.

The repository cap remains three active role subagents, including reviewers. These are
ordered candidates, not authorization to exceed the cap or implement unrelated backlog.

### Design checkpoints that can stop a proposed refactor

- If a supposed duplicate encodes a different permission, consent or lifecycle rule, keep
  the distinction explicit and share only the common mechanics.
- Use the first three vertical slices to forecast the reviewed inventory; do not create
  a large LOC target that forces abstractions or deletion of useful tests.
- If a new delivery helper needs every domain's flags and statuses, narrow its boundary;
  do not create a universal workflow engine.
- If a performance proposal lacks measured query growth or timing evidence, keep it as
  a hypothesis until the bounded measurement packet is complete.
- If R1/R2 or boot/worker compatibility evidence is missing, retain the current release
  safeguards. `OPS-03` and `SYS-09` are not permission to switch production phases.
- If an old `sending` or placeholder-terminal record has no log, classify it as unknown
  until transport state can be established. Missing evidence does not authorize resend.
- If a refactor needs a route or API contract removal, first inventory clients and define
  deprecation/rollback. Internal consolidation should not force an avoidable client migration.

## Existing work to reuse

The current issue-state snapshot contains 22 closed and 25 open children in the range
`#1499`–`#1545`. The separate CDN issue `#1384` is outside that count. The roll-up
[issue #1546](https://github.com/AI-Shipping-Labs/website/issues/1546) still contains older
embedded status tables; inspect each child before acting. This audit made no tracker edits.

| Existing open work | Use in this plan | Required check before reuse |
|---|---|---|
| [#1517](https://github.com/AI-Shipping-Labs/website/issues/1517), private/no-store responses | Account/privacy boundary work | Inspect response middleware and affected routes; reproduce missing headers rather than assume the old finding persists everywhere |
| [#1519](https://github.com/AI-Shipping-Labs/website/issues/1519), concurrent signup | Identity concurrency work | Race the current signup/newsletter paths and check side effects |
| [#1522](https://github.com/AI-Shipping-Labs/website/issues/1522), unverified-user purge | Bounded background processing | Measure current query growth and safe retention filters |
| [#1524](https://github.com/AI-Shipping-Labs/website/issues/1524), tag scans/ownership | Shared tag/query work | Preserve account-tag and content-tag semantics when sharing mechanics |
| [#1525](https://github.com/AI-Shipping-Labs/website/issues/1525), webhook attempts | Webhook concurrency work | Cover both event ownership and attempt allocation; a counter constraint alone does not deduplicate side effects |
| [#1526](https://github.com/AI-Shipping-Labs/website/issues/1526), homepage counts | Query-budget work | Verify the present homepage/card call chain and bounded count annotations |
| [#1527](https://github.com/AI-Shipping-Labs/website/issues/1527), startup configuration reads | Configuration/boot work | Separate startup measurements from request-time caching already fixed elsewhere |
| [#1528](https://github.com/AI-Shipping-Labs/website/issues/1528), ambiguous task deletion | Shared plan-client work | Delete succeeds on server but response is lost; client refresh must not restore a phantom row |
| [#1529](https://github.com/AI-Shipping-Labs/website/issues/1529), inline confirmations | Safe template/client work | Exercise apostrophes, quotes and HTML-like text through the browser |
| [#1530](https://github.com/AI-Shipping-Labs/website/issues/1530), [#1531](https://github.com/AI-Shipping-Labs/website/issues/1531), [#1532](https://github.com/AI-Shipping-Labs/website/issues/1532) | Accessibility and shared controls | Preserve toggle state semantics, questionnaire labels and keyboard typeahead behavior during UI consolidation |
| [#1533](https://github.com/AI-Shipping-Labs/website/issues/1533), runtime settings | Configuration ownership | Register/read each actual runtime setting through the existing framework |
| [#1534](https://github.com/AI-Shipping-Labs/website/issues/1534), [#1535](https://github.com/AI-Shipping-Labs/website/issues/1535) | API/Studio/domain separation | Move behavior to its owning domain, not another endpoint-private helper |
| [#1536](https://github.com/AI-Shipping-Labs/website/issues/1536), sync helper copies | Small deletion-first sync cleanup | Search imports, bound methods, tests and string references before deletion |
| [#1537](https://github.com/AI-Shipping-Labs/website/issues/1537), worker formatting | Shared worker projection | API and Studio render the same task values/errors through one tested owner |
| [#1538](https://github.com/AI-Shipping-Labs/website/issues/1538), datetime serialization | Small API consistency cleanup | Preserve timezone, null and error contracts; avoid a broad serialization framework |
| [#1539](https://github.com/AI-Shipping-Labs/website/issues/1539), Logfire updates | Worker/runtime configuration lifecycle | Verify documented effect across already-running processes, not just database persistence |
| [#1540](https://github.com/AI-Shipping-Labs/website/issues/1540), recurring schedules | Reliable background work | Check missing, stale, invalid and duplicate schedules without weakening startup availability policy |
| [#1541](https://github.com/AI-Shipping-Labs/website/issues/1541), URL query encoding | Shared filters/templates | Preserve multiple parameters and reserved characters across paging/filter changes |
| [#1542](https://github.com/AI-Shipping-Labs/website/issues/1542), duplicate course rows | Template reduction | One semantic record per item still supports desktop/mobile layouts and accessibility |
| [#1543](https://github.com/AI-Shipping-Labs/website/issues/1543), unreachable templates | Deletion inventory | Prove no view, include, dynamic renderer or supported legacy route uses each candidate |
| [#1544](https://github.com/AI-Shipping-Labs/website/issues/1544), Markdown renderers | Shared safe rendering | Preserve supported Markdown features while rejecting active content |
| [#1545](https://github.com/AI-Shipping-Labs/website/issues/1545), iframe names | Template/accessibility cleanup | Browser-accessible names remain descriptive after component consolidation |

These backlog references are not additional independently confirmed findings in the new
catalog. Their scope must be revalidated. They ensure existing work is not silently lost
when organizing the broader simplification program.

Closed work includes campaign claim/recovery, content-sync symlink protection, credential
handling during merge, password-reset reuse, deactivated-user API access, cache/query
improvements, recording-worker recovery, event-registration-row concurrency (#1518), and several deployment
gates. Preserve those changes. A new defect in a neighboring generic path should be scoped
separately rather than reopening the historical claim without evidence.

## Decisions that should remain explicit

- Preserve the modular monolith and current public product capabilities.
- Keep production/API/worker policy in domain owners; retain separate staff and member authorization boundaries.
- Do not flatten domain-specific retries into a generic automatic retry loop.
- Do not strip sanitizer, access, idempotency, privacy or release-compatibility checks to shorten code.
- Do not squash historical migrations while supported deployments or rollback images depend on them.
- Do not make a broad dependency upgrade or renderer replacement part of a small correctness fix.
- Do not replace database-backed query work with process-local caches without cross-worker invalidation semantics.
- Do not create a new test-policy framework to enforce every recommendation in this document.

## Review limits and follow-up evidence

This audit did not execute a full Django or browser suite, reproduce races against the
production PostgreSQL database, inspect live users/jobs/email logs, benchmark live pages,
review AWS Terraform, verify provider behavior, or run a dependency-CVE scan. Proposed
performance gains, backlog reduction and deployment retirement depend on those narrower
follow-up checks. Concurrency assertions in the catalog should be converted into deterministic
tests before a fix is accepted. Where SQLite cannot demonstrate a production invariant,
use the existing PostgreSQL test workflow or an isolated equivalent.

The previous audit had many findings subsequently fixed. Closed issue state is evidence
of tracking status, not automatic proof that every neighboring path is safe. Conversely,
an old audit's open table is not current issue state. The present catalog retains only
current inspected findings and explicitly marks candidates needing further validation.

## Audit deliverable checks completed

| Check | Result |
|---|---|
| Whole tracked Python syntax/structure inventory | 2,576 files parsed without syntax errors; measurements retained in the baseline JSON |
| Ruff advisory and existing metric reporter | Collected as diagnostic evidence; advisory output is not a passing correctness gate |
| Focused offline demonstrations | CLI credential routing, URL-segment truncation and table formatting; unsafe Markdown preservation; Book Club anonymous predicate; analytics failure propagation; nullable uniqueness |
| Django system check | Identity reviewer ran `uv run python manage.py check` successfully; expected local-development/startup warnings were recorded in its report |
| Independent plan review | Nine requested corrections resolved and accepted, including removal of unsupported deletion quotas and preservation of recovery/test/release boundaries |
| Cross-review of operational claims | Ruled out the apparent ORM-queue rollback and settings-import cache defects; tightened ambiguous-delivery and S3 provenance requirements |
| Finding-to-packet mapping | All 57 unique finding IDs mapped to packets A–M; no omitted finding |
| Source references and document links | 287 source-path/start-line references verified; all relative links and explicit finding anchors valid |
| Document scope | The three audit paths classified as docs-only through `scripts.affected_tests.build_plan`; no application/browser suite required for these paths |
| Final coverage pass | All 14 first-party modules under `static/js` received review; the eight account/event/admin/Studio follow-up modules passed `node --check`, and two additional findings were added with focused evidence |
| Proposed test commands | All 11 focused Django test modules named in the catalog exist; execution remains part of the implementation issues |
| Whitespace and repository state | `git diff --check` passed; only the three new audit artifacts were added by this task |

The working tree also contains the two pre-existing CLI edits, so its complete affected-test
plan is not docs-only. Those edits were preserved and their tests were not claimed as verified
by this audit. The document checks above apply to this task's three new audit artifacts.

### Requirement-to-evidence checklist

| Requested outcome | Evidence in the saved deliverables |
|---|---|
| Audit the website across its codebase | All 20 first-party entries in the current `INSTALLED_APPS` have coverage in the catalog; separate reviews cover templates, browser code, CLI, tests, build and deployment tooling |
| Find bugs and complicated logic | Findings identify current entry points, violated behavior or state invariants, confidence and reproduction requirements; length/complexity metrics are search evidence only |
| Find duplication and ways to remove code | Exact-body duplicate candidates are preserved in the baseline; domain findings identify shared owners and concrete code/test deletion candidates |
| Reduce project complexity significantly | The plan consolidates event/plan writes, rendering/access rules, external delivery and transport ownership; it rejects cosmetic file splitting and unsupported global deletion quotas |
| Use subagents | Three domain reviewers delivered source-backed findings; a separate review pass accepted the plan after nine corrections |
| Provide a very detailed plan with checks for every finding | Every catalog finding has evidence, a fix sequence, regression/acceptance checks, effort and rollout guidance; every ID maps to a sequenced work packet |
| Save the work in docs | The plan, complete finding catalog and machine-readable baseline are present under `_docs/audits/`, linked above and validated as docs-only additions |

The implementation checks and rollout gates are instructions for the subsequent fix issues.
They are not claims that the reported defects have already been repaired. The requested
deliverable here is the audited evidence and actionable plan.
