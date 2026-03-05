## Context

Forklift currently performs post-container authorship rewrite in the run workspace and then force-pushes the rewritten `{branch}` to GitHub `origin/{branch}`. The current rewrite command targets `refs/heads/{branch}`, which can rewrite the full reachable branch history instead of only post-upstream merge commits. This creates a high-risk review experience and can make branch lineage appear detached after force push.

The new workflow must keep post-run safety while moving publication to local-only handoff. The branch name is configurable (`{branch}`), not fixed to `main`.

## Goals / Non-Goals

**Goals:**
- Constrain rewrite scope to commits in `upstream/{branch}..HEAD` in the agent workspace.
- Eliminate automated post-agent pushes to GitHub remotes.
- Publish rewritten output to local repository branch `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}`.
- Preserve rollback/recovery clarity through explicit logging and stable, inspectable outputs.

**Non-Goals:**
- Managing or validating downstream GitHub PR workflows after local handoff.
- Adding collision-avoidance for same-second branch naming.
- Changing container-side merge behavior or agent instruction policy.

## Decisions

### 1) Rewrite only the post-upstream range
Forklift will compute and rewrite only the commit range between `upstream/{branch}` and workspace `HEAD`.

**Rationale:** This enforces the intended blast radius and avoids rewriting branch ancestry that predates the merge target.

**Alternatives considered:**
- Rewrite full `refs/heads/{branch}`: rejected due to excessive history mutation.
- Skip rewrite entirely: rejected because operator authorship normalization remains required.

### 2) Remove post-agent network publication
Forklift will not reattach/push to GitHub `origin` during post-run handling.

**Rationale:** Publishing rewritten history remotely before review is too destructive for normal operations.

**Alternatives considered:**
- Keep push with opt-in flag: deferred; still encourages risky direct publication.
- Push to remote review branch: rejected for now because this workflow is intentionally local-first.

### 3) Publish to local namespaced branch
Forklift will publish rewritten branch tips from the agent workspace to local repo target `upstream-merge/{timestamp}/{branch}`.

**Rationale:** Keeps local `{branch}` untouched, avoids non-bare checked-out branch update problems, and gives operators a clean review head that can be pushed manually later.

**Alternatives considered:**
- Force-update local `{branch}` directly: rejected due to dirty-tree and checked-out-branch hazards.
- Maintain snapshot branch + direct overwrite: rejected because namespaced handoff is safer and easier to reason about.

### 4) Keep ancestry verification and expand handoff logs
Forklift retains upstream ancestry verification and logs explicit local inspection commands plus resulting branch name.

**Rationale:** Safety checks remain mandatory even when publication target changes.

## Risks / Trade-offs

- **[Risk] Range-selection mistakes could still rewrite unintended commits** → **Mitigation:** verify rewrite boundary against `upstream/{branch}` pre/post rewrite and fail if invariant is broken.
- **[Risk] Operators may assume local publication implies remote publication** → **Mitigation:** explicit “no GitHub push performed” summary log.
- **[Risk] Branch namespace growth in local repos** → **Mitigation:** document that `upstream-merge/*` branches are disposable review artifacts.

## Migration Plan

1. Update post-run rewrite pipeline to compute bounded range and remove remote reattachment/push steps.
2. Add local publication step to `upstream-merge/{timestamp}/{branch}`.
3. Update summaries/README language from PR creation to local handoff workflow.
4. Rollback strategy: revert to previous behavior via git history if needed; no data loss occurs because publication is local-only.

## Open Questions

- None for this change; timestamp collision handling is intentionally deferred by operator preference.
