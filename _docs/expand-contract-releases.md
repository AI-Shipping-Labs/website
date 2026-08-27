# Expand/contract releases

Reusable procedure for any rolling deployment where old and new web/worker
images overlap and a schema change must stay compatible with both. The pattern
splits a risky migration into three separately reviewed, separately shipped
phases — R1 (expand + compatibility), R2 (activate), R3 (contract) — so a
rollback never has to reverse a migration.

Per-release records (the exact SHAs, immutable image tags, the migration
inventory for that release, and the promotion-evidence log) live alongside the
dated audit in `_docs/audits/` (for example
`_docs/audits/2026-07-21-expand-contract-release-1266.md`). This document holds
only the release-agnostic procedure.

## Work-vocabulary manifest

Each phase is defined by the "work vocabulary" the running images may produce
and consume. The manifest for a given release names the schedules and producers
that belong to each phase; record that per-release list in the release audit.
The reusable rules are:

- R1 must delete, and must not register, any R2-only schedule. Enumerate the
  R2-only schedule names for the release in its audit and confirm they are
  absent while R1 is live.
- R1 direct-producer rules: producers keep emitting the legacy task
  arguments/retry vocabulary the production worker already understands; they do
  not create or wake the R2-only durable jobs, outbox tasks, or encrypted-only
  paths. Existing task names/arguments that both images understand may continue.
- `website.release_phase` is compile-time artifact state. Never replace the R1
  gate with an `IntegrationSetting` or an environment/operator toggle — the
  phase must be baked into the image so it cannot drift at runtime.

## R1 — expand and compatibility

- [ ] `makemigrations --check --dry-run`, system checks, OpenAPI drift,
  collectstatic, the Django shards, PostgreSQL migration compatibility, and
  Playwright Core pass.
- [ ] Adding a physical NOT NULL column to an existing table keeps a valid,
  persistent `db_default` for the full old/new-image overlap window. A Python
  `default` and `AddField.preserve_default` are migration-state behavior; they
  do not provide a default when an old image omits the unknown column from its
  SQL after the migration. If no suitable database default exists, use an
  explicitly reviewed expand/contract shape that keeps the column nullable
  during overlap. Check the graph locally with
  `uv run python scripts/check_migration_safety.py`.
- [ ] The frozen prior-release migration matrix passes historical reads and
  writes (including concurrent/duplicate rows that the expand must tolerate).
- [ ] Any physical-drift fixture (a database that applied an earlier target's
  migration bytes) converges through the forward reconciliation migration.
- [ ] No migration removes a compatibility column, tightens a deferred nullable
  field, or adds a deferred constraint.
- [ ] Web rolls first and serves the exact immutable `/ping` tag, then the
  worker rolls; a web failure produces an executable prior-task-definition
  recovery command and no worker update.
- [ ] The R2-only schedules are absent and every incompatible direct producer
  uses its production-compatible behavior.
- [ ] Record dev tag/run, tester report, PM acceptance, production run/tag, and
  old-task drain/soak evidence in the release issue.
- [ ] During R1, rollback only changes both images back to the recorded
  production baseline tag; never reverse migrations. Redeploy the exact accepted
  candidate image to forward-recover and run reconciliation before the worker
  consumes queue work.

The recovery boundary is image-only. Keep the expanded schema, restore both
services to the recorded production baseline if necessary, allow its compatible
writes, then redeploy the exact candidate and run reconciliation. Never reverse
migrations, substitute a predeploy task for overlap proof, use a database
tunnel/manual SQL, or require direct provider-console access.

Any exact-image rollback rehearsal is intentionally R1-scoped: its script checks
the candidate's compile-time phase before pulling the old image and self-skips
once the R1 compatibility flag becomes false. Remove the rehearsal workflow step
and the production-tag pin in the separately reviewed R2 activation commit; do
not carry the old-image artifact dependency beyond its rollback window.

## R2 — activate without contract DDL

- [ ] Confirm the R1 web and worker tasks fully replaced the prior release and
  old queue work drained.
- [ ] Build a separately reviewed immutable artifact that enables the R2-only
  schedules and durable trigger/onboarding producers.
- [ ] Switch runtimes to their target paths (e.g. encrypted-only) and stop
  mapping/shadow-writing compatibility columns, while leaving their physical
  columns/defaults intact.
- [ ] Reconcile every compatibility/default row and attach or explicitly retain
  every staged row before enabling producers.
- [ ] Soak representative minute/five-minute/daily tasks and the affected flows.
- [ ] R2 rollback target is R1, never the pre-R1 release.

## R3 — contract

- [ ] Confirm all R2 tasks run code that ignores compatibility columns and
  temporary database defaults/nullability.
- [ ] Fail closed if any compatibility rows, overlapping running jobs, or
  compatibility backlogs remain.
- [ ] In a separately reviewed artifact, remove the compatibility columns,
  temporary database defaults, staging tables, and deferred nullability; add the
  final constraints.
- [ ] Prove the R2 artifact runs against the contracted schema before R3 is
  eligible for production.
- [ ] R3 rollback target is R2; never reverse destructive data migrations and
  never select R1 or the pre-R1 release after contract DDL.
