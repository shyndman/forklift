## Why
Forklift currently launches the sandbox without setting a Git identity, so `git commit` either fails (missing config) or quietly reuses the human’s identity from the cloned repo. Even when the agent finishes a rebase successfully, the host CLI simply leaves the rewritten branch inside the run directory and the operator must manually cherry-pick, amend authors, and push. Those manual steps are error-prone (easy to rewrite the wrong repo) and undocumented. We need a deterministic, push-button flow: agents should always commit as `Forklift Agent`, the host must remember the human’s identity at the start of the run, and Forklift must rewrite/push the branch automatically with safety rails so the human receives a PR-ready branch without touching Git plumbing.

## What Changes
- **Guarantee sandbox commits succeed.** During harness bootstrap run `git config --global user.name "Forklift Agent"` and `git config --global user.email forklift@github.com`, then log the values so debugging is straightforward. This removes any reliance on host-side gitconfig.
- **Capture the human identity before cloning.** As part of CLI startup:
  1. Run `git config user.name` / `git config user.email` inside the operator repo.
  2. If either value is missing, print an actionable error telling the human to run `git config --global user.name ...` and exit before doing any work.
  3. Persist the captured name/email (e.g., in `RunDirectoryManager` metadata) so later stages can reference them without re-reading the host repo.
- **Record a baseline for safe pushing.** Right after `ensure_required_remotes()`/`fetch_remotes()` succeed in `cli.py`, capture both:
  - the upstream SHA already stored today via `RunDirectoryManager._capture_branch_info`; and
  - the current `origin/<main_branch>` SHA (the fork tip).
  Persist these values (plus the human identity and remote URLs) into `metadata.json` before `_remove_remotes()` runs so post-run logic has an exact lease + tag target and knows how to reattach remotes even though they were stripped from the workspace.
- **Automate rewrite/push using the run workspace.** After the container exits successfully:
  1. Check for modified/untracked files (DONE.md, STUCK.md, logs). If any exist, run `git stash push -u -m "forklift-authorship-rewrite"` so nothing is lost.
  2. Recreate the stripped remotes inside `workspace/` using the URLs saved in metadata, then run `git fetch origin` and `git fetch upstream` so the workspace has fresh refs.
  3. Verify `git filter-repo` is installed (exit with a friendly error if not). Execute it with a `name-or-email` callback that rewrites every commit authored/committed by `Forklift Agent <forklift@github.com>` to the cached human identity. Run this command **only inside** the run workspace path.
  4. Create a local tag named `forklift/<branch>/<timestamp>/pre-push` pointing to the stored origin SHA. Never push this tag upstream; it is strictly for rescue operations.
  5. Force-push the now-rewritten branch to `origin/<branch>` using `--force-with-lease=<branch>:<stored_origin_sha>` so we bail out if the remote moved.
  6. If a stash was created, run `git stash pop` and warn the operator if conflicts occur so they know to recover the files manually.
- **Explain the automation loudly.** After the push, emit a structured log/CLI message that calls out (a) authorship was rewritten to `<name> <email>`, (b) the branch was force-pushed, (c) the backup tag path, and (d) whether the stash was reapplied.
- **Document the workflow and prerequisites.** Update `README.md` (and any other surfaced docs) with: how to set the global Git identity, the new `git filter-repo` requirement, what Forklift does after runs, and how to recover from the local backup tag or stash if something goes wrong.
- **Spell out installation options for `git filter-repo`.** Document that Forklift now depends on `git filter-repo` (verified latest release 2.47.0) and give operators clear installation avenues that satisfy its upstream requirements (`git >= 2.22`, `python >= 3.6`). Examples to call out: `pip install git-filter-repo==2.47.0`, macOS `brew install git-filter-repo`, or downloading the single `git-filter-repo` script from <https://github.com/newren/git-filter-repo/releases>. The CLI should validate `git filter-repo --version` before running the rewrite step and print remediation instructions referencing these options if it is missing.

## Capabilities

### New Capabilities
- `agent-authorship-rewrite`: Ensures every sandbox commit uses the standard Forklift Agent identity, then automatically rewrites those commits back to the human’s `user.name`/`user.email`, tags the previous fork tip for safety, and force-pushes the cleaned branch to `origin` after confirming the remote has not advanced.

### Modified Capabilities
- (none)

## Impact
- **Host CLI:** `src/forklift/cli.py` gains identity validation, metadata capture, the stash/tag/rewrite push pipeline (likely in `_post_container_results`), and richer completion logs. `run_manager.py` needs to store the operator identity + origin baseline SHA when preparing the workspace.
- **Sandbox harness / Docker image:** `docker/kitchen-sink/harness/run.sh` (and rebuilt image) must set the Forklift Agent identity every time the container starts.
- **Tooling dependency:** Operators must have `git filter-repo` installed on the host (minimum git 2.22 / python 3.6 per upstream docs, latest release 2.47.0). Forklift should check for it, confirm the binary responds (e.g., `git filter-repo --version`), and fail early with remediation steps that point to the supported install methods (pip, Homebrew, standalone script download).
- **Documentation:** Update `README.md`, design docs, and any troubleshooting guides to describe the automatic rewrite/push flow, the backup tag naming scheme, and how to recover a stash if the auto-pop fails.
