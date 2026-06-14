## Context

Forklift drives conflict resolution by launching OpenCode inside the kitchen-sink
container and mediating git at the binary level. Two processes run today:

- A long-lived server, `opencode serve`, started by `docker/kitchen-sink/opencode/start_server.sh`.
- A per-conflict client, `opencode run --attach http://127.0.0.1:<port>`, launched by
  `orchestrate.py` (conflict lifetime mode kills the client and relaunches a fresh
  session per conflict, threading prior resolution notes forward).

Mediation is a separate-process affair. The bash shim `docker/kitchen-sink/harness/includes/bin/git`
detects a paused rebase and execs `python3 -m forklift_harness.mediate`, which
classifies the command (`classify_paused_rebase_command` in `rebase_state.py`),
runs the real transition, emits host events on the events socket, then reports the
transition over the intra-container control socket (`control.py`) and blocks for
the orchestrator's directive.

The failure that motivates this change: during a paused rebase the shim mediates
**every** git call. OpenCode runs its own per-step workspace snapshots
(`git --git-dir <opencode snapshot> --work-tree /workspace write-tree`, `git init`,
`git config`). The classifier doesn't recognize them, so each is rejected with
exit 1 and logged as `Unsupported paused rebase command shape` — 181 of 883 lines
(~20%) in run `danger-pi_20260613_140442` — while OpenCode's snapshot feature
fails closed for the whole window. The root cause is structural: we intercept git
globally because we don't own the agent loop, which couples us to OpenCode's
private, evolving git usage.

The fix is to own the loop. An in-process Pydantic AI agent (`pydantic-ai==1.99.0`,
already a dependency) removes the only opaque source of internal git, lets
mediation move into a Python layer we control, and collapses the server/client
split and the intra-container control socket.

This spec depends on and modifies the contracts in
`openspec/changes/.../imperative-rebase-management` and
`specs/cross-container-rebase-events`.

## Goals / Non-Goals

**Goals:**
- Remove OpenCode (`serve`, `run --attach`, `start_server.sh`, the server/client split) and run the agent in-process via Pydantic AI + pydantic-ai-harness.
- Mediate git in-process through a custom `run_command` toolset layered above the harness `ShellToolset`, parsing commands with `bashlex`.
- Adopt the **target-repo discriminator**: mediate only git that resolves to the workspace repo carrying the live paused rebase; pass everything else through. Reject `GIT_*` environment overrides.
- Demote the binary shim to a defensive backstop enforcing the same rule for grandchild git.
- Replace log-parsing telemetry with direct structlog events and `result.usage()` metrics.
- Preserve the mediated vocabulary, single-conflict-per-session, the frozen continue-check, resolution notes, container isolation, and FORK.md handling exactly.

**Non-Goals:**
- Changing the rebase vocabulary or what the agent types (`git rebase --continue/--skip/--abort --resolution-note/--reason`, `git reset-conflict` stay).
- Changing the host-facing structured rebase event protocol (`FORKLIFT_REBASE_EVENTS_SOCK`, `REBASE_EVENT_VERSION`).
- Building a general-purpose coding agent. The agent's job is rebase conflict resolution; the capability set and prompt are scoped to that.
- Relaxing sandbox isolation. "Full shell" means the shell the agent has today — same constraints — not arbitrary container access.

## Decisions

### 1. Replace OpenCode with an in-process Pydantic AI agent

The agent loop runs inside the orchestrator process rather than as an attached
subprocess. This is what makes every downstream simplification possible: the
control socket becomes a function call, lifecycle becomes control flow, and
telemetry becomes direct logging.

*Alternatives considered:* (a) Keep OpenCode and teach the classifier to pass
through `--git-dir`-redirected snapshot calls. Fixes the spam but leaves us coupled
to OpenCode internals that change on each update — the recurring problem. (b) Keep
OpenCode but disable its snapshot feature. Not reliably configurable and still
opaque. Rejected in favor of owning the loop.

### 2. Capability set and the `run_command` override

The agent is constructed with pydantic-ai-harness capabilities: `filesystem`,
`shell`, tool search, and code mode (Monty sandbox). Both `filesystem` and `shell`
were verified to perform no internal git or workspace snapshotting, so they add no
opaque git source.

We do not use the stock `ShellToolset` directly. We layer a forklift toolset above
it that exposes our own `run_command(command: str) -> str`:

```
agent run_command(command)
  └─ parse command with bashlex
       ├─ contains a git invocation targeting the workspace repo?
       │     └─ mediate in-process (reuse classify + transition logic)
       └─ otherwise
             └─ delegate to harness ShellToolset.run_command(command)
```

The background-process tools (`start_command`, `check_command`, `stop_command`)
are **not** exposed — only synchronous `run_command` is mediated. This deletes the
`_background` lifecycle and the nasty cases (a daemon surviving a conflict-mode
session teardown).

Code mode wraps the assembled toolset into `run_code`, so the model invokes
`run_command` from sandboxed Python. Our override runs outside the sandbox (tools
execute normally); Monty constrains only the glue code.

*Alternatives considered:* subclassing `ShellToolset` vs. wrapping it. Wrapping is
preferred so harness upgrades to the underlying shell behavior flow through
unchanged.

### 3. Command parsing with bashlex, fail-closed on parse failure

The old shim received clean `argv` from the OS. The in-process `run_command`
receives a single command **string** run through a shell, so we must find git
invocations inside arbitrary shell. `bashlex` produces a bash AST; we walk it and
collect every command node whose first word is `git`, across `&&`/`||`/`;`,
pipelines, subshells, command substitution, env-prefixed commands, and redirects.
Extracted git argv feeds the existing `classify_paused_rebase_command` unchanged.

bashlex parses **syntax, not semantics** — it does no expansion, so aliases,
`g=git; $g …`, and `eval` defeat it. It is therefore the mediation front-end and
the common-case detector, **not** the security boundary (see Decision 6).

When bashlex cannot parse a command **and a rebase is paused**, we reject it as a
`ModelRetry` ("couldn't parse that, simplify it") so the agent rewrites it in
plainer shell. Reject-on-parse-failure is a property of the paused-rebase state
only; with no rebase in progress there is nothing to mediate and `run_command`
delegates unconditionally.

### 4. The target-repo discriminator and `GIT_*` rejection

A git invocation is mediated **only** when it resolves to the workspace repository
that holds the live paused rebase (`workspace_dir/.git`, the same path
`rebase_in_progress()` already inspects). Git targeting any other repository —
test temp repos, tooling git-dirs, OpenCode-style snapshot dirs — passes through
unmediated, **mutating verbs included** (`init`, `commit`, `checkout`,
`worktree`, even a nested `rebase`).

The target repo is resolved **from argv only**: the command's working directory
plus `-C`, `--git-dir`, `--work-tree`. Resolution uses real git
(`rev-parse --absolute-git-dir`) and compares to the workspace git-dir. Any
`GIT_*` environment variable present causes the invocation to be rejected
(fail closed) rather than resolved — the resolver trusts the auditable wire, never
the hidden environment. This is consistent with the existing scrub in `_git_env`,
which already neutralizes git config env.

For commands that **do** target the workspace repo, the existing
`classify_paused_rebase_command` is applied verbatim: the rebase vocabulary is
handled, read-only inspection (`ALLOWED_PAUSED_COMMANDS`) passes through, and
unsupported shapes are rejected. The mediation contract is unchanged; the
target-repo rule only decides *whether* a command enters mediation at all.

This single rule also retroactively eliminates the original spam: OpenCode's
snapshot calls carried `--git-dir <snapshot>`, a non-workspace repo, and would
pass through untouched.

*Alternatives considered:* a read-only-verb allowlist as the primary axis. Rejected
— it is both unnecessary (the continue-check that ran used no git) and insufficient
(real nested git from test suites is mutating but targets temp repos). Target repo
is the axis that separates the cases correctly.

### 5. In-process mediation, lifecycle as control flow

With agent and orchestrator in one process, the intra-container control socket
(`FORKLIFT_REBASE_CONTROL_SOCK`) and `control.py` (the `TransitionReport`/`Directive`
report-and-wait protocol) retire; the mediation handler calls orchestrator logic
directly. The host events socket (`FORKLIFT_REBASE_EVENTS_SOCK`) is untouched.

The two lifetime modes become control flow rather than socket directives:

```
git rebase --continue (workspace)  ── in-process handler ──┐
                                                           │ record note
                                                           │ run frozen continue-check
                                                           │ advance via run_real_git
                                                           ▼
                              ┌──────────────── rebase mode ───────────────┐
                              │ return next conflict state as tool string;  │
                              │ the SAME agent.run continues                │
                              └─────────────────────────────────────────────┘
                              ┌─────────────── conflict mode ──────────────┐
                              │ end THIS agent.run; the loop starts a fresh │
                              │ agent.run with continuity notes for the     │
                              │ now-current pause                           │
                              └─────────────────────────────────────────────┘
```

rebase mode is a plain tool-result return. conflict mode ends the current run from
inside the tool via the settled mechanism — `agent.iter()` with a mutable `deps`
flag and an early `break` (see "ending a run from a tool" in the API Reference) —
then the loop starts a fresh session.

The hard logic is reused, not rewritten: `rebase_state.py` (classification,
REBASE_HEAD identity, progress, continue-check, reset-conflict, clean-empty-stop)
is reusable nearly as-is; `mediate.py:main()` becomes the git branch of
`run_command`; `orchestrate.py`'s socket loop becomes direct calls.

### 6. The binary shim as a backstop

The PATH shim stays, demoted to a backstop for grandchild git that never reaches
the in-process `run_command` — tests, builds, and the fork's continue-check that
shell out to git. It enforces the same target-repo rule using its own real cwd,
argv, and env (rejecting `GIT_*`):

- Workspace repo + read-only (`ALLOWED_PAUSED_COMMANDS`) → allow (continue-check / git's own recursion).
- Workspace repo + rebase-state mutator that did not come through the in-process path → refuse with "git is mediated; resolve through the harness."
- Any other repo → exec real git normally.

The backstop is the soundness boundary that covers bashlex's semantic blind spot:
whatever obfuscation produced a git exec, the backstop sees the real command at
exec time.

### 7. Telemetry: direct logging, not parsing

The OpenCode-stream parsers — `clientlog_renderer.py`, `clientlog_command.py`
(`forklift clientlog`), and `post_run_metrics.py` — are **retired**, not rewritten.
Owning the loop, telemetry comes from the source and surfaces at the **top level**:

- Agent steps, tool calls, and rebase transitions emit structlog events at their
  call sites, folded into the existing `run=<id>` correlator stream the operator
  already sees live. There is no separate client-log file and no separate viewer
  command — the `forklift clientlog` command and the `opencode-client.log` file go away.
- Run cost is **exact**, not approximate: a run uses one configured model
  (`FORKLIFT_MODEL`) and cost is linear in token counts, so pricing the aggregated
  `result.usage` once (`calc_price(result.usage, FORKLIFT_MODEL)`) equals summing
  per-request prices. Per-request pricing would only matter for multi-model runs or
  mid-run tier-threshold pricing, neither of which applies.

### 8. Server removal

`start_server.sh` is removed. `docker/kitchen-sink/opencode/entrypoint.sh` drops
its server half: `start_server.sh` invocation, `server.ready`/`server.pid`
markers, the health-gate wait, `/run/opencode`, and `OPENCODE_SERVER_*`. It keeps
mount-ownership restore, the cleanup trap, and `runuser -u forklift` into the
harness. The `opencode#8502` password TODO disappears with the server.

### 9. Model and provider configuration

Keep the host-side pattern: the CLI loads provider config and passes it into the
container as env (the renamed loader, config file `forklift.env`). Pydantic AI
1.99.0 reads provider keys directly (verified): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY`, and for Google `GOOGLE_API_KEY` (with `GEMINI_API_KEY`
fallback). pydantic-ai does NOT read `GOOGLE_GENERATIVE_AI_API_KEY` — that key is removed.

New env surface:

- `FORKLIFT_MODEL` — the Pydantic AI model id (`provider:model`), replacing `OPENCODE_MODEL`.
  Verified 1.99.0 prefixes: `openai:` (aliases `openai-chat:`/`openai-responses:`),
  `anthropic:`, `openrouter:`, `google:` (deprecated `google-gla:`→`google:`,
  `google-vertex:`/`vertexai:`→`google-cloud:`).
- `FORKLIFT_MODEL_EFFORT` — a reasoning-effort knob mapped to the provider's
  thinking/reasoning setting via pydantic-ai `model_settings`.
- Provider API keys above, passed straight through.

Removed: `OPENCODE_MODEL`, `OPENCODE_AGENT`, `OPENCODE_SERVER_PASSWORD`,
`OPENCODE_API_KEY`, `OPENCODE_VARIANT`, and `GOOGLE_GENERATIVE_AI_API_KEY`. The
config file `opencode.env` → `forklift.env` and `opencode_env.py` is renamed
accordingly. The default `FORKLIFT_MODEL` is `openrouter:google/gemini-3.1-flash-lite-preview` (`OPENROUTER_API_KEY` is already present in the operator's `forklift.env`).

### 10. Agent system prompt (implementor-authored)

The conflict-resolution system prompt is written by the implementor, not specified
here. Constraints:

- Build on the context forklift already gives the agent — the FORK.md body, the
  instructions payload, and the per-conflict continuity notes — rather than
  duplicating it. Review that existing material first.
- Assume a capable model: convey how this rebase flow works without over-explaining
  git rebasing itself.
- **Get `theirs`/`ours` right — they are inverted from intuition.** In a rebase,
  `ours` = the upstream code being rebased onto, and `theirs` = the fork's own
  commits being replayed. So "theirs" is the code under the fork's control and
  "ours" is upstream — backwards from what the words suggest, because the rebase
  replays the fork's commits onto upstream HEAD. The prompt (and any conflict-side
  labeling) must state this explicitly so the agent never resolves the wrong side.

## Verified API Reference (implementation-ready)

Source-verified against installed/pinned versions: `pydantic-ai 1.99.0`,
`genai-prices 0.0.66`, `bashlex 0.18`, and `pydantic-ai-harness` (GitHub). The
implementor should not need to look any of this up.

### Dependency versions — one BLOCKING compatibility constraint

- `pydantic-ai==1.99.0` (pinned) exposes everything needed: `Agent`, `pydantic_ai.capabilities` (`AbstractCapability`, `CapabilityOrdering`, `ToolSearch`, `AgentCapability`), `pydantic_ai.tools.ToolSelector`, `pydantic_ai.exceptions.ModelRetry`, `agent.iter()`. No mismatch with the harness's capability imports.
- **BLOCKING:** `pydantic-ai-harness` `main`/latest requires `pydantic-ai-slim>=1.105.0`, **incompatible** with the `pydantic-ai==1.99.0` pin. Resolutions: pin the harness to a 1.99-compatible release (reported **v0.3.0**, targeting `pydantic-ai-slim>=1.95.1`) or bump `pydantic-ai` to `>=1.105.0`. Decision required before `uv add` (Open Questions).
- code-mode extra: `code-mode` and `codemode` both work; both pull `pydantic-monty>=0.0.16`.

### Pydantic AI — Agent, capabilities, custom toolset

- `Agent(model=None, *, system_prompt: str|Sequence[str]=(), instructions=None, tools=(), toolsets: Sequence[AgentToolset]|None=None, capabilities: Sequence[AgentCapability]|None=None, end_strategy='early', retries=None, ...)`.
- A custom toolset is passed as an **instance** via `toolsets=[my_toolset]` and MUST be an `AbstractToolset` (subclass `FunctionToolset`); a plain non-toolset wrapper is rejected. Lazy per-run build: pass a `ToolsetFunc` `(RunContext)->AbstractToolset|None`.
- Assembly order: function tools → explicit `toolsets=` → dynamic toolset factories → capability-contributed toolsets; capability wrappers apply outermost-first (first capability = outermost). Duplicate tool names across toolsets raise `UserError`.
- `ToolSelector = Literal['all'] | Sequence[str] | dict[str,Any] | callable`.

### Pydantic AI — run, usage, result

- `agent.run(...)`/`run_sync(...)` → `AgentRunResult`. `agent.iter(...)` → async-context-managed `AgentRun`, async-iterable over `UserPromptNode`/`ModelRequestNode`/`CallToolsNode`; final `agent_run.result`.
- **Correction:** `result.usage` is a **property** (callable-compat shim), not a method — use `result.usage` (calling it still works but warns).
- `RunUsage` fields: `input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`, `input_audio_tokens`, `cache_audio_read_tokens`, `output_audio_tokens`, `details`, `requests`, `tool_calls`. `total_tokens = input + output`. `request_tokens`/`response_tokens` are deprecated aliases; there is no single `cache_tokens`.
- `result.output`, `result.all_messages()` (`list[ModelMessage]`). Tool-call detail: `ModelResponse.tool_calls`, `ToolCallPart(tool_name/args/tool_call_id)`, `ToolReturnPart(tool_name/content/tool_call_id)`.

### Pydantic AI — ModelRetry (confirmed)

- `from pydantic_ai.exceptions import ModelRetry`. Raising it from a tool yields a `RetryPromptPart` returned to the model; any other exception propagates and ends the run. Budget is per-tool: `Agent(retries=N)` / `@agent.tool(retries=...)`; exceeding it raises `UnexpectedModelBehavior`. Our bashlex-parse-failure → `ModelRetry` plan is correct.

### Pydantic AI — ending a run from a tool (SETTLED)

Mechanism (verified in `tmp/pydantic-ai/pydantic_ai_slim/pydantic_ai/run.py` +
`_agent_graph.py`): drive the run with `async with agent.iter(prompt, deps=deps) as run:`
and iterate nodes manually. `build_run_context` sets `RunContext.deps` to the shared,
mutable user `deps` object, so the git-transition tool flips a flag on it
(e.g. `deps.transition_done = True`) after a successful conflict-mode `--continue`.
The loop checks the flag after each node and `break`s — before the next
`ModelRequestNode` runs, so no extra model round-trip occurs. `run.usage()` reads
`graph_run.state.usage` live (valid even after an early break) for cost; `run.result`
is `None` until an `End` node, which is fine because the transition is a tool
side-effect, not the run's output.

- **conflict mode:** the tool sets `deps.transition_done`; the loop breaks; the
  orchestrator starts a fresh `agent.iter` for the next conflict with continuity notes.
- **rebase mode:** the tool returns the next-conflict state and never sets the flag;
  the same run continues until the rebase completes and the model ends naturally.

No sentinel exception, no output-tool gymnastics, no `end_strategy` dependence. The
heavier `CallDeferred`/`DeferredToolRequests` path exists for resume-the-same-run
semantics and is deliberately not used, since conflict mode wants a fresh session.

### Pydantic AI — ToolSearch (auto-injected)

- `from pydantic_ai.capabilities import ToolSearch` (impl in private `_tool_search`). **Auto-injected into agents in 1.99.0** — you generally do not add a separate package. `ToolSearch(strategy=None, max_results=10, ...)`; `defer_loading=True` tools hide behind a `search_tools` tool until discovered; CodeMode wraps around it so `search_tools` stays native.

### pydantic-ai-harness capabilities (public)

- `from pydantic_ai_harness import FileSystem, Shell, CodeMode`.
- `FileSystem(root_dir='.', allowed_patterns=[], denied_patterns=[], protected_patterns=['.git/*','.env','.env.*','*.pem','*.key','**/secrets*'], max_read_lines=2000, max_search_results=1000, max_find_results=1000)`. Tools: read_file/write_file/edit_file/list_directory/search_files/find_files/create_directory/file_info. `.git/*` is write-protected and dot-dirs hidden from walkers by default — acceptable (the agent does git via shell; reads of known `.git/...` paths are still allowed).
- `Shell(cwd, allowed_commands=[], denied_commands=[], denied_operators=[...], default_timeout=30.0, max_output_chars=50_000, persist_cwd=False, allow_interactive=False, env=None, denied_env_patterns=[])` builds a `ShellToolset` that **unconditionally** registers `run_command`, `start_command`, `check_command`, `stop_command`. **There is no first-class switch to drop the background tools or replace `run_command`.** Therefore the design's "layer above ShellToolset" means: do NOT use the `Shell` capability directly — build a forklift `FunctionToolset` subclass that exposes only our `run_command`, and internally delegates non-git commands to a private `ShellToolset` instance's `run_command`. Register it via `toolsets=[...]`.
- `CodeMode(tools='all', max_retries=3)` — wrapper capability; `get_wrapper_toolset()` wraps the assembled toolset in `CodeModeToolset` (sandboxed vs native split + one `run_code`). Our custom toolset is wrapped automatically when in the assembled set and matched by the selector. Monty: no `subprocess`, no third-party imports, no classes, no timing primitives; allowed stdlib `sys/typing/asyncio/math/json/re/datetime/os/pathlib`; tools execute OUTSIDE the sandbox via `tool_manager.handle_call`.

### bashlex 0.18

- `bashlex.parse(s, strictmode=True, ..., proceedonerror=False)` → list of top-level nodes (`command`/`pipeline`/`list`/`compound`); `parsesingle(s)` → one node. One generic `bashlex.ast.node`; `.kind` selects meaning; `command.parts` holds `word`/`redirect`/`assignment`; a `word` node's literal is `.word`.
- Traversal: subclass `bashlex.ast.nodevisitor`, override `visitcommand(self, n, parts)`; return `False` to stop recursing a subtree.
- Parse failures (all → our fail-closed reject during a paused rebase): `bashlex.errors.ParsingError`, `bashlex.tokenizer.MatchedPairError` (a ParsingError), and `NotImplementedError` for unsupported constructs (arithmetic `$((..))`, complex `${param#word}`). Default `proceedonerror=False` IS the fail-closed mode. Heredoc and process substitution ARE supported (contra the earlier assumption).
- git-argv collector:
  ```python
  class V(bashlex.ast.nodevisitor):
      def visitcommand(self, n, parts):
          argv = [p.word for p in parts if p.kind == 'word']
          if argv and argv[0] == 'git':
              self.found.append(argv)
  ```

### genai-prices 0.0.66

- `from genai_prices import calc_price, Usage`. `calc_price(usage: AbstractUsage, model_ref: str, *, provider_id=None | provider_api_url=None, genai_request_timestamp=None) -> PriceCalculation`.
- `RunUsage` is structurally an `AbstractUsage` → pass `result.usage` directly. `PriceCalculation.input_price/.output_price/.total_price` are `Decimal` USD.
- **Single-model runs price exactly:** forklift configures one `FORKLIFT_MODEL` per run and cost is linear in token counts, so `calc_price(result.usage, FORKLIFT_MODEL)` once equals per-request summation. Per-request pricing (`ModelResponse.cost()`) is only needed for multi-model runs or mid-run tier-threshold pricing — out of scope here.

### Forklift internals (verified)

- **Logging** (`src/forklift/cli.py::_configure_logging`, renderer `src/forklift/logs.py`): structlog + stdlib `ProcessorFormatter`; columns `timestamp/level/run/event/kv`. Correlator: `structlog.contextvars.bind_contextvars(run=run_paths.run_id)` bound in `Forklift.run()` right after `RunDirectoryManager.prepare()`. Emit: `logger = structlog.get_logger(__name__)`, then `logger.info("event", key=value, ...)`; non-reserved kwargs land in `kv`. `--debug` only flips the stdlib level. Reuse the existing `run` contextvar — no new correlator object.
- **container_runner.py**: `docker run --rm --name forklift-<...> -v workspace:/workspace -v harness-state:/harness-state -v opencode_logs:/home/forklift/.local/share/opencode/log -v control_dir:/forklift-control ... <image> /opt/opencode/entrypoint.sh`. Env forwarded as sorted `-e KEY=VALUE`, merged from `OpenCodeEnv.as_env()` + `build_container_env()` (`FORKLIFT_MAIN_BRANCH/RUN_ID/AGENT_LIFETIME/HOST_UID/HOST_GID`, optional `TZ`) + `_build_container_env()` (injects `FORKLIFT_REBASE_EVENTS_SOCK=/forklift-control/rebase-events.sock`). Host events: `_start_rebase_event_listener(...)` binds an AF_UNIX socket (107-byte path limit, chmod 666), daemon thread reads newline-delimited JSON, `_parse_rebase_event` accepts only `v==REBASE_EVENT_VERSION`. `REBASE_EVENT_VERSION=1` is duplicated here and in `rebase_state.emit_event`. Events socket is independent of the control socket. `SENSITIVE_ENV_KEYS={OPENCODE_API_KEY, OPENCODE_SERVER_PASSWORD}` and `HARNESS_ENTRYPOINT=/opt/opencode/entrypoint.sh` change when OpenCode is removed.
- **Telemetry consumers**: `post_run_metrics.py` parses `opencode-client.log` into a usage/metrics summary; `clientlog_renderer.py` + `clientlog_command.py` render that log for `forklift clientlog`; `cli_post_run.py` wires the post-run summary. All are OpenCode-stream parsers → retire/replace with structlog + `result.usage`.
- **HarnessConfig.from_env** field→env: `workspace_dir←WORKSPACE_DIR`, `harness_state_dir←HARNESS_STATE_DIR`, `real_git_bin←REAL_GIT_BIN`, `main_branch←FORKLIFT_MAIN_BRANCH`, `upstream_ref←UPSTREAM_REF` (default `upstream/<main>`), `continue_check_file←REBASE_CONTINUE_CHECK_FILE`, `client_log←CLIENT_LOG`, `events_sock←FORKLIFT_REBASE_EVENTS_SOCK` (empty→None), `control_sock←FORKLIFT_REBASE_CONTROL_SOCK`, `agent_lifetime←FORKLIFT_AGENT_LIFETIME`, `git_user_name/email←FORKLIFT_GIT_USER_NAME/EMAIL`, `git_editor←FORKLIFT_GIT_EDITOR`, `conflict_index_snapshot←FORKLIFT_CONFLICT_INDEX_SNAPSHOT`. **No `run_id` field** on HarnessConfig (host `FORKLIFT_RUN_ID` lives in `RunPaths`).
- **Control-socket removal sites**: `includes/runtime_env.sh`, `harness/run.sh`, `opencode/start_server.sh`, `rebase_state.py` (`control_sock`), `control.py` (whole file), `mediate.py` (`send_report_and_wait`), `orchestrate.py` (`ControlListener` loop), tests `test_harness_rebase.py`, `test_opencode_entrypoint.py`. Non-trivial coupling is `mediate.py ↔ orchestrate.py` via `send_report_and_wait`/`ControlListener`/`TransitionReport`/`Directive`; the rest is mechanical.
## Implementation Contract (decided)

These decisions close the integration gaps so the work is buildable without reverse-engineering.

### Agent deps + loop/tool division of labor (deps shape, preserved duties)

The agent runs with a shared, mutable deps object:

```python
@dataclass
class AgentDeps:
    state: RebaseState            # reused; wraps HarnessConfig (workspace, real_git, ...)
    report: RunReport             # reused; accumulates resolutions/skips/stuck
    lifetime: str                 # "conflict" | "rebase" (FORKLIFT_AGENT_LIFETIME)
    lock: asyncio.Lock            # serialize rebase-state mutation across run_code fan-out
    transition_done: bool = False # set True to end the current run (break the iter loop)
    terminal: int | None = None   # finalize_* exit code when the whole run is over
    relaunch: bool = False        # conflict-mode advance: start a fresh session next
```

The git-mediation `run_command` tool, for a workspace-repo transition verb, under `async with deps.lock`:
1. runs the existing mediation (continue-check gating, real git via `state.run_real_git`, builds a `TransitionReport`) — reuse `mediate.py`/`rebase_state.py` logic verbatim;
2. `deps.report.record(report)`; emits the structlog transition event and the host event (`state.emit_*`);
3. sets the loop signal — **abort** → `deps.terminal = finalize_stuck(); deps.transition_done = True` (still gated on a non-empty `STUCK.md`); **completed** → `deps.terminal = finalize_completed(); deps.transition_done = True`; **rebase-mode advance** → return the next-conflict state string, leave flags False (same run continues); **conflict-mode advance** → `deps.transition_done = True; deps.relaunch = True`;
4. returns a tool-result string (ignored when the loop breaks).

The orchestrator loop replaces `run_agent_loop`/`_handle_transition` (no socket):

```python
deadline = monotonic() + agent_timeout
while True:
    if remaining <= 0: return finalize_timeout()
    deps.transition_done = deps.relaunch = False
    async with agent.iter(build_payload(), deps=deps) as run:
        async for _ in run:
            if deps.transition_done: break
    if deps.terminal is not None: return deps.terminal
    if deps.relaunch: continue                       # fresh session; continuity notes via build_payload()
    return finalize_completed() if not state.rebase_in_progress() \
           else finalize_failed("agent exited without completing the rebase")
```

Preserved from current `orchestrate.py` unchanged: `RunReport`→`rebase-report.json`, `harness-status.txt`, `finalize_completed/stuck/timeout/failed` (exit codes 0/0/2/1), `run_initial_rebase` (auto-skip clean empty stops; paused/completed/failed), `build_payload` (instructions + fork-context + conflict-mode continuity section), host events (`emit_paused_events`/`emit_complete_event`). Removed: `ControlListener`, `launch_opencode`/`_opencode_command`/`_kill_process_group`/`wait_for_exit`, and the `opencode_bin`/`server_port`/`variant` fields.

### In-container runtime & launch

- **Python runtime in the image:** a uv-built venv at `/opt/forklift/venv` (Python 3.13) with pinned `pydantic-ai`, `pydantic-ai-harness[code-mode]`, `bashlex`, `genai-prices`, `structlog`. Declare them in a new `docker/kitchen-sink/harness/py/pyproject.toml` for `forklift_harness` and install into the venv; the harness runs from that venv.
- **Launch chain (same shape, server removed):** `entrypoint.sh` (ownership restore + cleanup trap) → `runuser -u forklift` → `run.sh` (bootstrap: fork-context, setup command, initial-rebase prep) → `/opt/forklift/venv/bin/python -m forklift_harness.orchestrate`. No `start_server.sh`, no readiness/PID markers, no health gate.
- **File moves:** delete `docker/kitchen-sink/opencode/`; move the entrypoint to `docker/kitchen-sink/harness/entrypoint.sh`; update the Dockerfile `ENTRYPOINT` and `container_runner.HARNESS_ENTRYPOINT` to `/opt/forklift/harness/entrypoint.sh`.
- **Dockerfile:** remove the OpenCode install block and the `OPENCODE_VERSION`/`OPENCODE_HOME` ENV + PATH entry; drop the `opencode/*` COPYs; add the venv build.
- **Mounts/env:** drop the `-v <opencode_logs>:/home/forklift/.local/share/opencode/log` mount and the `opencode_logs` `RunPaths` field (agent telemetry goes to stdout + structlog, not a mounted dir). Rename `OPENCODE_TIMEOUT`→`FORKLIFT_AGENT_TIMEOUT` (default 600); drop `OPENCODE_SERVER_PORT`/`OPENCODE_BIN`/`OPENCODE_VARIANT`.

### Backstop shim

`docker/kitchen-sink/harness/includes/bin/git` stays a thin bash shim but execs a Python backstop (`/opt/forklift/venv/bin/python -m forklift_harness.backstop "$@"`) that **reuses the same target-repo resolver module** as the in-process toolset. It rejects on any `GIT_*` env; resolves the target repo from real cwd + argv; if the target is the workspace repo and the verb is a rebase-state mutator not allowed during a pause → refuse (nonzero + message); otherwise `exec` the real git binary. Read-only workspace verbs (`ALLOWED_PAUSED_COMMANDS`) and any non-workspace repo pass straight through. The resolver lives in one module imported by both the toolset and the backstop.

### Telemetry schema

Structlog events on the top-level `run=<id>` stream:
- `agent step` — `step` (int).
- `agent tool` — `tool` (name), `command` (echoed command for run_command), `ok` (bool), `duration_ms`.
- `rebase transition` — `action` (continue/skip/abort), `sha`, `subject`, `files` (count), `note`.

Post-run summary (replaces `post_run_metrics`; sourced from `result.usage` + `RunReport` + `genai-prices`), emitted as one structured event shown at the top level: `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `total_tokens`, `requests`, `tool_calls`, `cost_usd` (Decimal), `conflicts_resolved`, `skips`, `outcome`, `duration_s`.

### Model effort passthrough

`FORKLIFT_MODEL_EFFORT`, when set, is passed verbatim into pydantic-ai `ModelSettings['thinking']` (unified field accepting `'minimal'|'low'|'medium'|'high'|'xhigh'`); forklift does not validate or translate it. Unset → omit the field.

## Risks / Trade-offs

- **Conflict-mode "end the run after a transition"** → Settled, not a risk: manual `agent.iter()` + a mutable `deps` flag + early `break` (see the API Reference). No extra model turn; `run.usage()` stays readable.
- **bashlex coverage gaps and semantic blindness** → Fail closed on parse failure (ModelRetry) during the paused window; the backstop shim is the real enforcement, so a missed detection cannot mutate the workspace rebase undetected.
- **Code mode fans out `run_command` via `asyncio.gather`** → Serialize rebase-state mutation behind a lock so a `--continue` cannot interleave with another git call in the same `run_code` batch.
- **git's own recursion (hooks, merge drivers, `exec` rebase steps) hits the backstop with inherited `GIT_*`** → These target the workspace repo and are git-internal; the backstop allows workspace read-only and git's own recursion while refusing only agent-originated mutators that bypassed the in-process path. Validate against a real multi-conflict rebase.
- **Resolution quality regression vs. OpenCode's polished agent** → The task is narrow; ship a focused, forklift-owned conflict-resolution system prompt and tool schemas. Measure against recorded conflict runs.
- **Self-hosting correctness** → Forklift forking Forklift runs a suite that spawns real git in temp repos and drives the mediator. The target-repo discriminator must be correct out of the gate; cover it with tests that run nested git/rebases in temp repos while the workspace rebase is paused.
- **Monty sandbox restrictions** (no `subprocess`, limited stdlib in `run_code`) → Acceptable: the agent acts only through tool-functions, which execute outside the sandbox. Document the restriction in the agent prompt.

## Migration Plan

This is a clean cutover — no compatibility shims, no dual-runtime period.

1. Resolve the `pydantic-ai-harness` ↔ `pydantic-ai` version conflict FIRST (Open Questions), then `uv add` the chosen harness release (code-mode/Monty extra) and `bashlex`; `genai-prices` already satisfies.
2. Build the in-process loop with the settled end-run mechanism (`agent.iter()` + mutable `deps` flag + early `break`; see API Reference).
3. Build the forklift `run_command` toolset (bashlex parse, target-repo resolver, `GIT_*` rejection) reusing `classify_paused_rebase_command`.
4. Build the in-process agent loop in `orchestrate.py`: capability wiring, model config, rebase/conflict lifetime as control flow; remove `control.py` and the socket.
5. Demote `includes/bin/git` to the backstop policy.
6. Remove OpenCode: `start_server.sh`, the server half of `entrypoint.sh`, `opencode/` config; update the Dockerfile (drop OpenCode install, add agent runtime + Monty).
7. Replace telemetry: structlog events at the call sites surfaced at the top level; cost from `result.usage`. Retire `clientlog_renderer.py`, `clientlog_command.py` (`forklift clientlog`), `post_run_metrics.py`, and the `opencode-client.log` file.
8. **Rebuild the kitchen-sink image** after any `docker/kitchen-sink/` change (non-negotiable per AGENTS.md), then run an end-to-end rebase against a recorded conflict fixture.

*Rollback:* revert the change set; there is no runtime feature flag, since the two runtimes do not coexist.

## Open Questions

None outstanding. Resolved during verification: version pin (fixed); end-run mechanism (settled — `agent.iter()` + `deps` flag + early break); tool search (auto-injected, left default); config rename (`forklift.env`, `FORKLIFT_MODEL`/`FORKLIFT_MODEL_EFFORT`); system-prompt ownership (implementor — Decision 10); `forklift clientlog` (retired, logs to top level); cost (exact); default model (`openrouter:google/gemini-3.1-flash-lite-preview`).
