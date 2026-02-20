# Fork Context Template

Use this file to brief Forklift on what makes your fork unique. Keep it short and high-signal—the file is copied into every run workspace and echoed in the harness logs.

## Mission / Themes
- Why this fork exists
- Critical behaviors or features that must be preserved

## Test & Verification Guidance
- Commands to run when time allows (e.g., `uv run pytest`, `npm test`)
- Long running suites that may be skipped or deferred

## Risky Areas
- Paths or subsystems the agent should treat carefully (e.g., `/vendor`, `infra/`)
- Any generated or vendored assets that must not be touched

## Deployment / Coordination Notes
- Manual steps required after merges
- Contacts or Slack handles to tag in `STUCK.md`

## Example
```
Mission: Keep our offline vendor snapshot intact while rebasing weekly onto upstream/main.
Tests: Run `uv run pytest` first; skip `npm run e2e` unless time remains.
Risky Areas: Do not delete `/vendor` or `infra/pipelines/` contents.
Coordination: If npm scripts need changes, tag @forklift-admin.
```
