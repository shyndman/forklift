## Why

Forklift currently routes integration work through `Forklift.run()` in `src/forklift/cli.py`, which prepares run directories, launches the container, and performs post-run publication steps. The same CLI surface also includes a read-only `clientlog` subcommand (`subcommand: Clientlog | None`), so a new read-only `changelog` subcommand fits the existing command pattern and gives teams preflight visibility before any rebase workflow starts.

## What Changes

- Add a new host-side CLI subcommand: `forklift changelog`.
- Wire `changelog` using the same clypi command pattern already used by `Forklift` + `Clientlog` (`src/forklift/cli.py`, `src/forklift/clientlog.py`).
- Reuse the existing branch input contract already implemented in `resolved_main_branch()` (`src/forklift/cli_runtime.py`): `--main-branch` with default `main`, and compare `<main-branch>` against `upstream/<main-branch>`.
- Reuse existing remote helpers from `src/forklift/git.py` (`ensure_required_remotes`, `fetch_remotes`) so changelog fetch behavior matches orchestration preflight behavior.
- Produce terminal Markdown output (rendered with Rich) containing:
  - a net-diff change summary
  - predicted conflict locations and counts derived from deterministic Git analysis
- Use `git merge-tree` (with merge-base) as the conflict prediction source of truth.
- Keep conflict detection deterministic/imperative; use an LLM only to describe the net code changes.
- Reuse existing OpenCode env-loading conventions (`DEFAULT_ENV_PATH`, `load_opencode_env`, CLI override validation in `src/forklift/opencode_env.py` and `src/forklift/cli_runtime.py`) for model configuration inputs.
- Introduce a new host-side narrative client module because the current codebase has env/config plumbing but no existing host LLM request module.
- Fail the command if the LLM summary stage fails (no silent fallback).
- Keep existing `forklift` orchestration behavior unchanged by ensuring changelog does not call orchestration-only helpers (`RunDirectoryManager.prepare`, `ContainerRunner.run`, `_post_container_results`, authorship rewrite/publication helpers).

## Capabilities

### New Capabilities
- `changelog-command`: Adds a read-only preflight command that fetches refs, analyzes net branch deltas, predicts conflict hotspots, and renders Markdown changelog output.

### Modified Capabilities
- `forklift-orchestrator`: Clarifies command-surface requirements so the existing orchestration flow remains unchanged while `changelog` is added as a separate non-mutating command.

## Impact

- Affected code:
  - `src/forklift/cli.py` (subcommand registration and command routing)
  - `src/forklift/cli_runtime.py` (reuse of existing main-branch normalization/validation)
  - `src/forklift/git.py` (reuse plus possible extension of read-only git helpers)
  - `src/forklift/opencode_env.py` (reuse of existing model/env configuration loading)
  - `src/forklift/post_run_metrics.py` pattern for Rich Markdown rendering (`Markdown(...)`) can be reused for terminal output style
  - New changelog-focused command/analysis/llm/render modules under `src/forklift/`
  - New tests in the existing `tests/` unittest style suite (see patterns in `tests/test_cli_runtime.py` and `tests/test_clientlog.py`)
- Affected user behavior:
  - New `forklift changelog` command output in terminal Markdown
  - Existing `forklift` command semantics remain intact
- Dependencies/systems:
  - Continues using local Git CLI and configured remotes
  - Requires host Git with modern `merge-tree` support (Git 2.38+), because this feature depends on the modern merge-tree mode introduced in Git 2.38 and its conflict metadata output
  - Documents portability caveat for environments still on older Git builds (for example Git 2.34 on older LTS distros), which lack the modern merge-tree behavior this parser depends on
  - Uses already-declared dependencies in `pyproject.toml` (`rich`, `pydantic-ai`) rather than introducing a new package for v1
  - Keeps the current `pydantic-ai==1.59.0` dependency for v1 implementation; no additional LLM SDK is required for changelog narrative generation
  - Uses existing OpenCode env file path (`~/.config/forklift/opencode.env`) and safe-value validation rules
  - Optional future alternative (not selected for v1): direct provider SDKs (`openai`, `anthropic`) if the team later chooses provider-specific behavior over the current multi-provider `pydantic-ai` abstraction
