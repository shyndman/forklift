## 1. Split `Forklift` command internals

- [x] 1.1 Create focused `cli` support modules for post-run handling, authorship rewrite/publication, and ownership/env utilities.
- [x] 1.2 Move logic from `Forklift` methods into extracted modules while keeping the `forklift.cli` command entrypoint/import surface stable.
- [x] 1.3 Preserve and, where needed, update symbol exports used by existing tests that patch `forklift.cli` call points.

## 2. Split `clientlog` internals

- [x] 2.1 Create dedicated parser and event-model module(s) for transcript decoding and relative-time normalization.
- [x] 2.2 Create dedicated renderer module(s) for snapshot/follow formatting and tool-event rendering.
- [x] 2.3 Move command/follow-loop logic into a focused command module while keeping `forklift.clientlog.Clientlog` available.

## 3. Cleanup and guardrails

- [x] 3.1 Remove inert helpers/state discovered during extraction only after confirming no callers remain.
- [x] 3.2 Add or adjust targeted tests for extracted seams without changing user-facing behavior expectations.
- [x] 3.3 Run project checks used by this repo (targeted tests first, then broader checks) and fix only refactor-related regressions.
