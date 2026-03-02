## 1. Rewrite Scope Safety

- [x] 1.1 Update post-container rewrite flow to compute rewrite boundaries from `upstream/{branch}` to workspace `HEAD`.
- [x] 1.2 Replace full-branch rewrite invocation with bounded-range rewrite and add invariant checks that commits at/before `upstream/{branch}` are unchanged.
- [x] 1.3 Update residual-authorship validation so it evaluates the rewritten range behavior expected by the new bounded rewrite flow.

## 2. Local Publication Handoff

- [x] 2.1 Remove post-agent remote reattachment and GitHub `origin` push logic from the rewrite pipeline.
- [x] 2.2 Add local publication step that writes rewritten output to `upstream-merge/{YYYYMMDDTHHMMSS}/{branch}` in the local repository.
- [x] 2.3 Update rewrite result data structures and summary logging to report local handoff branch details and explicitly state that no GitHub push occurred.

## 3. Operator Experience and Verification

- [x] 3.1 Replace PR-stub messaging with local review handoff instructions (including branch name and suggested inspection commands).
- [x] 3.2 Update README and related workflow docs to describe bounded rewrite scope and local-only publication behavior.
- [x] 3.3 Add or update automated checks covering bounded rewrite and local publication branch creation paths.
