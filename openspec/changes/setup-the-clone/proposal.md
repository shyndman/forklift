## Why

Forklift often launches the agent before project dependencies are installed, which wastes run budget and increases avoidable failures during conflict resolution and verification. We need a deterministic, repo-owned bootstrap step that can prepare `/workspace` before the agent starts.

## What Changes

- Add optional `setup` front matter support in `FORK.md` for repository bootstrap commands.
- Run `setup` inside the harness before agent launch, in `/workspace`, with a strict 180-second timeout.
- Treat malformed front matter, setup timeout, setup non-zero exits, or post-setup dirty git state as fail-closed run failures.
- Log setup output to `/harness-state/setup.log`.
- Strip front matter from agent-visible context so metadata is harness-only.
- Keep `STUCK.md` reserved for agent-authored blocked-work outcomes.

## Capabilities

### New Capabilities
- _(none)_

### Modified Capabilities
- `agent-sandbox-run`: Update harness startup requirements to support optional pre-agent setup execution from `FORK.md` front matter, strict fail-closed behavior, and front-matter stripping from agent context.
- `opencode-agent-bridge`: Update deterministic launch requirements so setup executes before `opencode run`, while preserving fixed client launch behavior and transcript logging.

## Impact

- Affected code: `docker/kitchen-sink/harness/run.sh`, plus associated harness tests and docs.
- Affected run artifacts: new `/harness-state/setup.log`; no change to existing client/server log locations.
- Behavior impact: earlier, deterministic dependency bootstrap and fewer agent-side retries/redundant setup commands.
- Compatibility: existing `FORK.md` files without front matter remain valid and unchanged in behavior.
