## Context

Forklift currently copies `FORK.md` into `/workspace`, then the harness echoes that file into instructions and immediately launches `opencode run`. In many forks, successful merge work requires dependency bootstrap first (for example, `uv sync` or `bun install`) so that the agent can run project tooling without spending cycles on setup guesswork.

This change introduces a deterministic, repo-authored bootstrap step, while preserving two existing constraints:
1. deterministic harness launch behavior, and
2. `STUCK.md` semantics as agent-authored blocked-work output.

## Goals / Non-Goals

**Goals:**
- Add optional `setup` metadata to `FORK.md` front matter.
- Execute `setup` before agent launch in `/workspace`.
- Enforce fail-closed behavior for parse errors, setup failures, setup timeout, and post-setup dirty git state.
- Keep front matter harness-only by stripping it from agent-visible context.
- Produce auditable setup logs at `/harness-state/setup.log`.

**Non-Goals:**
- Introducing a general task runner or multi-step setup DSL.
- Changing host orchestrator run lifecycle/state machine.
- Using `STUCK.md` for infrastructure/bootstrap errors.
- Relaxing existing deterministic OpenCode launch requirements.

## Decisions

1. **Front matter contract: strict and minimal**
   - Decision: Accept YAML-like front matter only when it starts at line 1 with `---` and ends at the next `---` delimiter; parse a single optional key `setup` as string (including multiline block string values).
   - Why: strict placement and shape avoids ambiguous parsing and accidental metadata interpretation in free-form markdown.
   - Alternative considered: permissive parsing with leading blank lines and arbitrary keys. Rejected due to surprising behavior and higher parser complexity.

2. **Bootstrap execution boundary: container harness, not host**
   - Decision: Execute `setup` in `docker/kitchen-sink/harness/run.sh` before instruction/payload assembly and before `opencode run`.
   - Why: setup depends on container toolchain and should match the exact runtime environment used by the agent.
   - Alternative considered: host-side setup after clone. Rejected because host/runtime drift undermines reproducibility.

3. **Failure policy: fail closed with explicit logging**
   - Decision: Any front matter parse failure, `setup` non-zero exit, `setup` timeout (180s), or dirty tracked git state after setup causes immediate harness failure (non-zero exit) and prevents agent launch.
   - Why: invalid bootstrap state should not spend agent budget or produce misleading downstream failures.
   - Alternative considered: fail-open continuation. Rejected because it masks bootstrap defects and increases noisy failures.

4. **Agent context boundary: strip front matter**
   - Decision: Agent-visible context includes only `FORK.md` body (front matter removed) in `instructions.txt`, `fork-context.md`, and OpenCode payload.
   - Why: avoids duplicate setup execution by agents and keeps metadata channel separate from instruction channel.
   - Alternative considered: expose full file to agent. Rejected due to high likelihood of redundant setup reruns.

5. **Audit trail: dedicated setup log**
   - Decision: Append full setup stdout/stderr to `/harness-state/setup.log` and reference setup outcome in harness logs.
   - Why: bootstrap failures need a stable artifact distinct from agent transcript.
   - Alternative considered: client log reuse. Rejected because setup occurs before client starts and should stay separable.

## Risks / Trade-offs

- **[Risk] Strict parser rejects slightly malformed front matter users expect to work** → **Mitigation:** emit explicit, actionable error text to logs; document exact accepted format in `README.md` and `FORK.md` template.
- **[Risk] Dirty-tree fail-close may reject legitimate setup commands that update tracked files (for example lockfiles)** → **Mitigation:** document that `setup` must be non-mutating to tracked project files; keep command focused on dependency install into cache/artifacts only.
- **[Risk] 180-second setup timeout may be too short for some large dependency graphs** → **Mitigation:** keep timeout fixed initially for deterministic behavior; revisit with data if recurring failures show legitimate need.
