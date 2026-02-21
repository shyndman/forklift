## 1. Sandbox Identity and Requirements

- [ ] 1.1 Update `docker/kitchen-sink/harness/run.sh` (or equivalent entrypoint) to set `Forklift Agent <forklift@github.com>` via `git config --global` before the agent starts.
- [ ] 1.2 Document the new host prerequisite (`git filter-repo` availability + sandbox identity expectations) in `README.md` and any relevant docs.

## 2. Host Identity Capture & Metadata

- [ ] 2.1 Teach the CLI to resolve `user.name`/`user.email` from the operator repo at startup and fail fast when either is missing.
- [ ] 2.2 Extend run metadata to store the operator identity, main branch name, upstream SHA, and the pre-run `origin/<branch>` SHA for later tagging/leases.

## 3. Rewrite & Push Pipeline

- [ ] 3.1 Implement workspace stash handling (stash -u before rewrite, pop afterward with logging if conflicts) and reattach/fetch remotes post-run.
- [ ] 3.2 Integrate `git filter-repo` execution scoped to the run workspace to rewrite Forklift Agent commits to the captured operator identity; validate binary availability before use.
- [ ] 3.3 Create local-only backup tags referencing the stored baseline SHA and force-push the rewritten branch with `--force-with-lease`, emitting a clear summary to the operator.

## 4. Verification & Messaging

- [ ] 4.1 Add automated checks/logs ensuring rewritten commits carry the operator identity before pushing.
- [ ] 4.2 Update CLI completion messaging (and/or DONE.md guidance) to explain that authorship was rewritten, the branch pushed, tag location, and stash recovery instructions.
