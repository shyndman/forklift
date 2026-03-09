## Why

`forklift changelog` currently treats every changed path as equally relevant, which makes reports noisy for forks that intentionally carry large generated blobs (for example periodic JSON snapshots). Operators need a repo-level way to exclude known-noise files while still seeing transparent metrics that show what was filtered.

## What Changes

- Add changelog exclusion rules that use gitignore-style pattern semantics, including ordered rules and `!` negation support (last match wins).
- Allow changelog exclusion metadata to be declared in `FORK.md` front matter so settings live with fork-specific policy.
- Extend FORK front-matter parsing to accept a new `changelog` metadata block without breaking existing `setup` behavior.
- Canonicalize rename/copy paths to destination-path semantics before applying exclusions so filtering is deterministic and intuitive.
- Update deterministic output to show comparative metrics (`all files`, `excluding patterns`, and `delta`) plus active exclusion rules and matched-file counts.
- Ensure exclusions affect both deterministic metrics and predicted conflict hotspot sections for consistency.

## Capabilities

### New Capabilities
- `changelog-exclusions`: Configurable, gitignore-style exclusion filtering for changelog deterministic analysis and rendering.

### Modified Capabilities
- `agent-sandbox-run`: Expand `FORK.md` front-matter contract to allow a `changelog` metadata key in addition to `setup` while preserving strict validation.

## Impact

- Affected code:
  - `src/forklift/changelog.py` (load/merge exclusion config into command flow)
  - `src/forklift/changelog_analysis.py` (rule evaluation, rename canonicalization, baseline+filtered metric computation)
  - `src/forklift/changelog_models.py` (model both baseline and filtered deterministic summaries)
  - `src/forklift/changelog_renderer.py` (comparative metric table and exclusion transparency sections)
  - `docker/kitchen-sink/harness/includes/fork_context.sh` (front-matter parser accepts `changelog` metadata while keeping strict mode)
  - `tests/test_changelog.py` and harness parser tests (new filtering, negation, rename, and metadata parsing behavior)
- Affected user behavior:
  - `forklift changelog` can suppress configured noise paths and now reports baseline vs filtered metrics explicitly.
  - `FORK.md` can declare changelog exclusions once per repo instead of requiring command-specific prompting.
- Dependencies/systems:
  - No new runtime dependency required; use stdlib rule matching.
  - Requires documentation updates in `README.md` and `FORK.md` template for front-matter schema and exclusion examples.
