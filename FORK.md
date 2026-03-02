# Fork Context Template

Use this file to brief Forklift on what makes your fork unique. Keep it short and high-signal—the file is copied into every run workspace and echoed in the harness logs.
The harness forwards this text verbatim as the positional payload to `opencode run`, so treat it as the agent's single source of contextual truth.

## Mission / Themes

- Why this fork exists
- Critical behaviors or features that must be preserved

## Test & Verification Guidance

- We are in early testing. You are NOT to invoke tests at this time.

## Risky Areas

- Paths or subsystems the agent should treat carefully (e.g., `/vendor`, `infra/`)
- Any generated or vendored assets that must not be touched

## Deployment / Coordination Notes

- Manual steps required after merges
- Contacts or Slack handles to tag in `STUCK.md`

## Example
```
Mission: Keep our offline vendor snapshot intact while rebasing weekly onto upstream/main.
Coordination: If npm scripts need changes, tag @forklift-admin.
```
