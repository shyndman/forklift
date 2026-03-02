## Why

Forklift’s post-run history rewrite currently rewrites and force-pushes too broadly, making it hard to review and easy to destabilize branch lineage. We need a safer post-merge handoff that rewrites only the intended range and publishes results locally for human review.

## What Changes

- Restrict authorship rewrite scope in the agent workspace to commits in `upstream/{branch}..HEAD`, instead of rewriting all commits reachable from `{branch}`.
- Remove post-agent pushes to GitHub remotes from Forklift’s automated flow.
- Publish rewritten agent output to a local namespaced branch: `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}`.
- Preserve verification guarantees that `upstream/{branch}` remains an ancestor after rewrite.
- Update post-run logging so operators get explicit local review handoff instructions rather than PR creation guidance.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `forklift-orchestrator`: post-container rewrite scope and publication target requirements change from full-branch rewrite + GitHub push to bounded rewrite + local branch handoff.

## Impact

- Affected code: `src/forklift/cli.py` post-container rewrite/publish pipeline and summary logging.
- Affected behavior: no automated network publication after the agent run; publication target becomes the local repository.
- Affected operator workflow: review begins from local `upstream-merge/...` branches, with any GitHub push/PR handled manually outside Forklift.
