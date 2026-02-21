## Context
Host Forklift clones the operator repo into a timestamped run workspace, strips remotes, and launches the kitchen-sink container where the agent works. Today commits either fail (missing Git identity) or inherit the operator’s name/email, and the host leaves the rewritten history to humans. We need deterministic authorship inside the sandbox plus an automated, safe rewrite/push flow once the agent finishes so runs end with a human-authored branch without touching the operator’s working copy.

## Goals / Non-Goals

**Goals:**
- Ensure every agent run has a valid Git identity so commits never fail mid-run.
- Capture the operator’s Git identity up front and reuse it when rewriting agent commits.
- Automate rewrite/push entirely inside the prepared workspace clone, leaving the host repo untouched.
- Provide reversible safety rails: stash untracked artifacts before mutating history and tag the pre-run fork tip locally.
- Communicate clearly when the host rewrites/pushes on behalf of the user and where the backup tag resides.

**Non-Goals:**
- Supporting multiple user identities per run (we assume one name/email captured at start).
- Rewriting specs or artifacts post-implementation (the specs phase remains deferred).
- Providing remote backup tags or mirroring rewritten history back into the operator’s repo automatically.

## Decisions

1. **Sandbox identity seeding**
   - Bake `git config --global user.name "Forklift Agent"` and `git config --global user.email forklift@github.com` into the harness entry sequence so every run commits cleanly without relying on host gitconfig state.
   - Rationale: keeps the agent environment deterministic; no extra CLI knobs required.

2. **Host identity enforcement**
   - At CLI startup, call `git config user.name`/`user.email` within the operator repo. Abort if either is missing.
   - Cache these strings in memory for later reuse (metadata + rewrite).
   - Rationale: we need the operator identity before the agent makes commits so we know what to rewrite to; failing fast prevents ambiguous authorship.

3. **Baseline capture + metadata**
   - Extend `RunDirectoryManager._capture_branch_info` to also record `origin/<main_branch>` SHA (fork tip) in `metadata.json`.
   - Use this SHA for force-push lease enforcement and for the local-only safety tag.
   - Rationale: storing the baseline decouples rewrite/push from the operator working tree and enables rollbacks.

4. **Rewrite/push pipeline (post-run)**
   - Reuse the prepared `workspace/` rather than recloning. Sequence:
     1. Detect dirty state; if files are present (DONE/STUCK/logs), run `git stash push -u -m "forklift-authorship-rewrite"` so we can restore them later.
     2. Reattach `origin`/`upstream` remotes using URLs captured before removal (already known via `ensure_required_remotes`).
     3. Fetch both remotes to ensure the lease SHA is accurate.
     4. Run `git filter-repo --name-or-email-callback` (or config file) scoped to `Forklift Agent <forklift@github.com>` to rewrite author + committer to the operator identity, operating *only* inside the workspace path.
     5. Create local tag `forklift/<branch>/<timestamp>/pre-push` pointing to the stored baseline SHA; do **not** push the tag.
     6. Force-push the rewritten branch with `--force-with-lease=<branch>:<baseline_sha>` to `origin/<branch>`.
     7. If a stash was created, attempt `git stash pop`; if conflicts occur, warn the user to inspect `git stash list` instead of auto-merging.
   - Rationale: This flow keeps risky operations isolated, preserves escape hatches, and ensures remote state is updated atomically.

5. **User notification + docs**
   - CLI logs and finishing message must mention: authorship rewritten, branch pushed, backup tag path, stash status.
   - Update `README.md` (and possibly design docs) to describe the new trust contract and the requirement for `git filter-repo` on the host.

## Risks / Trade-offs
- **filter-repo availability** → Mitigated by declaring it a prerequisite in README and validating its presence before running the rewrite.
- **Stash artifacts failing to reapply** → Mitigate by naming the stash entry and logging instructions to recover manually if `stash pop` conflicts.
- **Force-push clobbering concurrent human work** → Mitigate with `--force-with-lease` against the stored baseline and by tagging the previous head.
- **Tag clutter** → Tags remain local by design; add cleanup guidance in documentation/logs.
- **Longer post-run time** → Acceptable trade-off; rewrite only runs on successful merges.

## Migration Plan
1. Update harness/Docker image to configure the agent identity; rebuild/publish the image.
2. Ship CLI changes that validate operator identity, capture baseline SHA, and run the rewrite/push pipeline.
3. Document the new prerequisite and workflow so operators know what to expect; release notes should highlight the automatic push behavior.
4. Optionally provide a one-off script to prune historical run directories or tags if operators choose.

## Open Questions
- Should we allow operators to opt out of automatic push (e.g., flag to leave rewritten workspace only)?
- Do we need to surface stash status (applied vs. left in stack) in DONE.md or host logs beyond CLI output?
- Is additional telemetry desirable to prove the rewrite succeeded before forcing pushes (e.g., verifying commit authors post-filter)?
