# Agent worktree lifecycle runbook

On-demand runbook for the orchestrator. `_docs/PROCESS.md` owns the issue
lifecycle and links here; read this file before creating a role worktree,
closing or adopting a lease, removing a worktree, pruning worktree metadata,
retiring an unmerged agent branch, or restoring a retired branch. Every command
and safety veto below is binding. The body was moved unchanged from
`_docs/PROCESS.md` by issue #1840.

### Agent worktree lifecycle

Agent worktree cleanup is an explicit orchestrator step, not a startup sweep or
an On-Call responsibility. The fail-closed helper stores lifecycle leases below
the repository's common Git directory, so the evidence survives role handoffs
and removal of an eligible worktree.

#### Create the lease before dispatch

From the clean shared main checkout, create a lease immediately after every
`git worktree add` and before dispatching a role into that path:

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" \
  lease-create --path .claude/worktrees/<name> --issue <issue> --role <first-role>
```

The active lease is authoritative across idle handoff gaps. Do not close it
while a SWE, Tester, PM-acceptance, or On-Call agent is running, waiting, or may
resume on the path. Every separately created role worktree gets its own lease.

#### Terminal handoff and dry run

On-Call reports the exact issue, merge SHA, successful `Deploy Dev` run ID/run
head, verdict, and all worktree paths it used, then returns without cleaning.
The orchestrator must use its active-agent registry to verify that every role
which can access a candidate has ended. The repository helper cannot query that
host registry; `--roles-ended` is an attributable assertion that this external
check was completed, not an inactivity detector.

Only after that check, close each matching lease from shared main. Closing
independently validates the successful GitHub run and Git ancestry:

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" \
  lease-close --path .claude/worktrees/<name> --issue <issue> \
  --merge-sha <merge-sha> --run-id <deploy-dev-run-id> \
  --run-head-sha <run-head-sha> --roles-ended

uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  classify --path .claude/worktrees/<name>
```

An issue that intentionally remains open for a `[HUMAN]` acceptance criterion
uses a separate, explicit terminal disposition. After the code is merged,
`Deploy Dev` is green, the `human` label is present, and every role has ended,
run one of these commands from shared main:

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" \
  lease-close --path .claude/worktrees/<name> --issue <issue> \
  --merge-sha <merge-sha> --run-id <deploy-dev-run-id> \
  --run-head-sha <run-head-sha> --roles-ended --human-pending

# Use only when lifecycle evidence is genuinely absent.
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" \
  lease-adopt --path .claude/worktrees/<name> --issue <issue> \
  --merge-sha <merge-sha> --run-id <deploy-dev-run-id> \
  --run-head-sha <run-head-sha> --roles-ended --human-pending
```

`--human-pending` is never inferred. It seals
`terminal.issue_disposition = "human_pending"` and requires the exact matching
issue in `AI-Shipping-Labs/website` to be `OPEN` with exactly one
case-sensitive `human` label entry. Other valid labels may coexist. Missing,
duplicate, renamed, differently cased, or malformed labels fail closed. A
closed issue is invalid in this mode even when its `human` label remains. In
ordinary mode, the issue must be `CLOSED`; a reopened legacy closed-issue lease
is never reinterpreted as human-pending. Existing terminal leases without a
disposition retain their closed-issue meaning.

Continue with the same read-only review and one-candidate apply sequence:

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  classify --path .claude/worktrees/<name>

uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  remove --apply --path .claude/worktrees/<name> --issue <issue> \
  --plan-digest <reviewed-plan-digest>
```

Record the exact removed path and merged branch, or the retained path with its
reason codes and evidence errors. Worktree and merged-branch removal is
operational cleanup only. The product issue remains open and labeled `human`
until a human verifies the pending criterion and closes it. The cleanup helper
uses only read-only `gh issue view`; it never edits, labels, comments on,
closes, or reopens the issue.

The default/no-subcommand invocation is also a read-only classification of all
registered worktrees plus existing unregistered directories under
`.claude/worktrees/`. Missing or malformed leases, dirty or untracked changes,
unmerged HEADs, active processes, incomplete `/proc` visibility, paths outside
the boundary, and shared main all remain protected.

Legacy paths without a lease require `lease-adopt` with the same terminal
evidence and `--roles-ended`, after the orchestrator checks its registry. Never
adopt a malformed lease or infer role completion from an absent OS process.

Leases created before `.claude/worktrees` became a symlink may be stored under
the historical shared-main alias path and its corresponding key. The helper
recognizes only the exact direct-child alias for the same canonical candidate;
canonical records remain primary, and malformed, alternate-root, nested,
duplicate, or canonical/alias-colliding evidence remains protected. A valid
historical active lease stays active. `lease-adopt` must never be used over
valid, malformed, ambiguous, or conflicting historical evidence. Ambiguous
classification, reconciliation, and adoption refusals report every conflicting
source filename and key in deterministic order for attributable manual review.
Lease evidence and migration artifacts must be lstat-verified regular files and
read without following symlinks. External, broken, looping, directory, FIFO, or
other non-regular entries are retained as attributable invalid evidence.

Review and migrate one historical record explicitly. For an active lease,
assert `--roles-ended` only after checking the authoritative active-agent
registry; OS process absence is not sufficient. The same assertion must be
present in the reviewed plan and apply request. The reconciliation digest also
binds the exact matching registration snapshot: canonical path, HEAD, branch
ref, detached, locked, and prunable state. Apply recomputes every field and
refuses same-registration drift before changing lease files. Every present
source, target, and migration artifact is additionally bound by no-follow file
identity and content digest. The digest also seals the complete directory-wide
manifest of `.json` entry names, kinds, identities, sizes, and content digests,
including records that were not associated with the reviewed candidate. Before
each authoritative create, link, publication, or quarantine move, apply
rechecks the boundary, registration, candidate existence, process snapshot,
role assertion, origin/main, attributable migration actor, and that complete
manifest, including expected absences and transaction-created entries. Recovery never discards
staged evidence until the canonical bytes are revalidated against it, every
legacy and temporary entry is still absent as reviewed, and the resulting
lookup has exactly one authoritative canonical record:

The reviewed digest contains the public lease-directory path, strict resolved
path, no-follow `lstat`, and independently opened directory device, inode, and
mode. Apply recomputes those facts before mutation and refuses a replacement
directory even if it contains hardlinks to every reviewed evidence inode. At
apply start, the helper opens the reviewed lease directory once with no-follow
semantics and pins the same device/inode/mode identity. Every transaction read,
create, link, and rename is relative to that descriptor. Before and after each
commit, the public lease-directory path must independently resolve and open
without following a link to the same pinned identity. A parent-directory
replacement can therefore neither redirect the transaction nor be mistaken
for success.

Migration never deletes mutable evidence. Exact reviewed entries move with
atomic no-clobber `renameat2` into unique non-`.json` quarantine names. The
moved inode is verified after the syscall; a foreign regular file or symlink is
atomically moved back before refusal. Successful migration permanently retains
the last-known-good source and temporary evidence in quarantine. Those entries
are ignored by authoritative lookup and require a separate future review if
they are ever cleaned. Failed temporary writes are likewise retained rather
than risking deletion of a replacement. Immediately after writing and syncing
`.migration-new`, its still-open descriptor identity, exact bytes, actor, and
time are bound before the directory sync. Any pathname swap refuses.

Each quarantine syscall also receives the exact complete `.json` manifest
expected immediately before and after that one transition. If evidence appears
inside the scan-to-`renameat2` window, the helper no-clobber renames the exact
moved inode back to its reviewed pathname, syncs the directory, verifies the
restored snapshot and absence of the quarantine name, and only then refuses.
The newly appeared evidence is never changed. A refusal may not leave the
reviewed source moved merely because the drift arrived at the syscall boundary.

The exact reviewed legacy source is hardlinked to the staged recovery name
before canonical publication. Recovery explicitly recognizes every adjacent
staged, canonical-linked, and quarantined state. For compatibility with an
interruption at the older link-first boundary, the exact
legacy+canonical+`.migration-new` state without a staged name is recoverable
only when the legacy record is the one strict historical alias, canonical and
new are the same regular inode and bytes, their lifecycle meaning and
provenance exactly match that source, the actor is the current attributable
actor, and no additional JSON evidence exists. Recovery then recreates the
staged hardlink before any further authoritative transition.

Every recovery continuation is actor-bound. The operator applying a recovery
plan must be the same attributable actor recorded in `path_migration`; a
different actor cannot complete or inherit another operator's interrupted
transaction. Cross-actor recovery remains fail-closed with every artifact
unchanged for the original actor or manual review.

Final success is the last transaction action. It rescans the pinned directory
and public lookup for canonical, legacy-alias, or temporary collisions,
revalidates the exact reviewed canonical provenance, and then rescans the
complete sealed `.json` manifest after public lookup returns. A directory entry
appearing during that final lookup refuses before success is recorded. Nothing
is deleted or otherwise mutated after that proof.

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  lease-reconcile --path .claude/worktrees/<name> --roles-ended

uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  lease-reconcile --apply --path .claude/worktrees/<name> \
  --roles-ended --plan-digest <reviewed-reconciliation-digest>
```

Terminal records do not need a new role assertion, but their stored
`roles_ended` evidence, sealed closed-issue or human-pending issue disposition,
successful `Deploy Dev`, run-head, merge, and ancestry evidence are revalidated
before migration. Reconciliation changes only the stored path/key, preserves
the exact disposition and other lifecycle meaning, and records the old alias
key plus migration actor/time. It never closes, adopts, prunes, or removes
anything. After migration, run a fresh normal classification and use a separate
cleanup digest/request; migration success is not cleanup eligibility.

#### Remove one unchanged eligible candidate

Review/post the dry-run record. Apply only the exact candidate and digest from
that record:

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  remove --apply --path .claude/worktrees/<name> --issue <issue> \
  --plan-digest <reviewed-plan-digest>
```

Apply recomputes every fact, re-fetches the sealed issue evidence immediately
before the first Git mutation, and refuses drift. It uses non-force
`git worktree remove`. Before a later merged-safe `git branch -d`, it revalidates
the issue, run, ancestry, lease, process, removed-path, branch-tip, and
`origin/main` facts. A refusal at that later boundary reports the worktree
removal as completed and retains the recoverable branch. It never recreates a
worktree, uses recursive deletion, or uses force. Run one candidate at a time
and report both removed and retained paths/reason codes.

Already-absent stale Git registrations are a separate mode:

```bash
uv run python scripts/cleanup-agent-worktrees.py \
  --repo . --actor "orchestrator:<session-id>" --json prune-metadata
```

Metadata apply additionally requires the exact aggregate digest and refuses the
whole prune if any stale registration is locked, active, unmerged, ambiguous,
or lacks validated terminal evidence. It never deletes a directory. An
existing unregistered directory or broken `.git` backlink is retained for
manual recovery. Local branches are retained by default; include
`--delete-merged-branches` in both the reviewed dry run and apply invocation to
request explicit post-prune `git branch -d` cleanup. The option is part of the
plan digest and never force-deletes an unmerged branch. Stale classification,
apply, and any requested post-prune branch deletion revalidate the same sealed
closed-issue or human-pending disposition and refuse issue-label drift.

Do not run cleanup at role/process startup, role launch, merge, push, watcher
start, cancellation, timeout, hang, superseded/no-verdict result, or while any
role or process may still use the path. Current incident/recovery paths are not
retroactively removable merely because this helper exists.

#### Unmerged agent branch retirement

Normal worktree cleanup never invokes archive retirement automatically. An
unmerged branch remains `RETAIN_UNMERGED_HEAD`, and that attributable retention
is a valid cleanup-gate outcome. Retirement is a later, separate operator
decision; it is not part of startup, role launch, merge, push, On-Call,
cancellation, timeout, worktree removal, metadata prune, or lease maintenance.

For one exact agent branch, review a read-only archive-retirement plan only
after the branch is unattached and its owning issue has exact accepted
supersession plus terminal-green evidence:

```bash
uv run python scripts/retire-agent-branches.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  archive-retire --branch worktree-agent-<issue> --roles-ended
```

The `--roles-ended` flag is an attributable assertion that the orchestrator
checked the authoritative active-agent registry. The helper also refuses active
or malformed leases, live or incompletely scanned processes, recovery
directories, attached worktrees, open/`human`/blocked issues, incomplete Git or
GitHub evidence, already-merged tips, multi-commit divergence, and unique
patches without an exact Alexey/Valeria supersession decision.

Positive owner evidence is machine-readable, not inferred from prose. A
terminal decision comment must contain exactly the marker
`agent_branch_terminal_v1` followed by one JSON line with only `decision` set
to `accepted-terminal-green` and a decimal-string `run_id`. Unique-patch
supersession must contain exactly `agent_branch_supersession_v1` followed by one
JSON line with only `decision` set to `retire-as-superseded`, exact `branch`,
full `tip`, full `replacement_commit`, and a non-empty `reason`. Extra prose,
unknown fields, duplicates, ambiguity, negation, or a non-owner author fail
closed. Duplicate JSON keys and repeated identical evidence comments are also
ambiguous; exactly one positive structured authorization may qualify. Both
lines must be ASCII with one LF between them, no leading/trailing blank line,
wrapping, carriage return, non-breaking space, or Unicode lookalike. The JSON
must be the exact compact, lexicographically key-sorted serialization. A
withdrawal, retention instruction, `never` statement, or other contradictory
owner comment coexisting with positive evidence protects the branch. The
record stores the exact immutable GitHub comment URL for each authorization;
restore re-fetches those comments and accepts them only while their entire body
still matches the canonical schema and decision enum.

Apply accepts one exact agent branch and the unchanged reviewed digest. Before
an expected-old-tip compare-and-delete it compare-creates an immutable,
tip-addressed backup ref, creates an annotated local archive ref, and writes a
common-Git-dir retirement record. It independently verifies all three:

```bash
uv run python scripts/retire-agent-branches.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  archive-retire --branch worktree-agent-<issue> --roles-ended \
  --apply --plan-digest <reviewed-plan-digest>
```

The backup, archive, and record are permanent local recovery evidence; the
helper never pushes, expires, deletes, or rewrites them. The backup ref name
binds the exact branch and tip and is never removed, including on rollback or a
collision. A separate digest-bound restore compare-and-creates only the
original local branch at that exact tip. The record and annotated archive must
bind the requested branch, branch-derived issue, tip, and both derived recovery
refs exactly. Restore leaves all recovery evidence intact and does not check
out or create a worktree.

The common Git directory is pinned by its resolved no-follow directory inode
and rechecked before every mutation. The record directory and file are opened
relative to pinned no-follow directory handles and must remain regular objects
physically inside that directory. Lifecycle lease scanning uses the same
common-Git-relative contract and seals every entry's kind, inode, complete
bytes, and metadata. It also seals the lease directory's no-follow path and
descriptor identity even when the directory is empty. Symlinked, replaced,
malformed, changed, or incompletely scanned lease evidence protects the branch.
Recovery scanning seals the configured entry lstat identity, symlink target,
resolved directory inode, complete entry manifest, and scan-completeness state.
A broken, looped, unreadable, replaced, or identity-changing recovery boundary
also protects it.

Apply holds the exact record inode locked and revalidates its content and
identity at the mutation boundary. Source deletion is one atomic Git ref
transaction that verifies the exact annotated archive-ref OID, permanent
backup tip, and source tip before deleting. Restore verifies the archive and
backup and compare-creates the source in one transaction. Both flows verify
the locked record and every worktree/lease/process/recovery fact immediately
before and after the transaction. Retirement also refetches the full issue,
labels, owner-comment authorization, workflow run, and main/replacement
provenance immediately before and after source deletion. Any authority drift
fails closed and compare-recreates an absent source from the sealed tip. A
failed post-delete condition
compare-creates an absent source from the sealed tip without trusting a drifted
archive; a collision leaves the permanent backup intact. A failed restore is
rolled back only while the permanent backup still proves reachability.

Restore never treats matching local record/archive/backup bytes as sufficient
authority. It derives the issue from the requested exact branch, fetches that
exact issue again, and requires the safe closed state, allowed owner, exact
same-issue patch match or canonical supersession, stored authorization comment
URLs and bodies, replacement ancestry, and exact successful terminal run ID,
head, result, and workflow. These facts are digest-bound and revalidated before
and after compare-create, so a locally forged cross-issue recovery triplet
cannot establish retirement provenance.

Review and apply restore separately:

```bash
uv run python scripts/retire-agent-branches.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  restore --branch worktree-agent-<issue> --roles-ended

uv run python scripts/retire-agent-branches.py \
  --repo . --actor "orchestrator:<session-id>" --json \
  restore --branch worktree-agent-<issue> --roles-ended \
  --apply --plan-digest <reviewed-restore-digest>
```

Neither flow checks out commits, creates/removes worktrees, merges, rebases,
cherry-picks, resets, commits, pushes, prunes, touches remote refs, kills
processes, or mutates lifecycle leases. Never use this helper as a bulk sweep,
and never treat an audit table or similar commit subject as deletion authority.

The post-green handoff is a delivery-pipeline completion gate for every
worktree used by the issue. Report every removed path exactly. For every path
that remains protected, report the exact classifier reason codes and errors;
that attributable retention report completes the gate for the path but never
authorizes force, recursive deletion, fabricated evidence, role cancellation,
or bypassing any safety veto. Until #1443 provides a repository bridge, the
authoritative external active-agent registry check remains required before the
orchestrator asserts `--roles-ended`; OS process absence is not role-completion
evidence.
