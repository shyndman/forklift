## ADDED Requirements

### Requirement: Files command lists current fork-owned paths
The CLI SHALL provide `forklift files` as a read-only command that lists paths currently owned by the fork. A path is fork-owned when it exists on the selected local main branch but is absent from `upstream/<main-branch>`.

#### Scenario: Default branch analysis
- **WHEN** the user runs `forklift files` without overriding branch flags
- **THEN** the command analyzes `main` and `upstream/main`
- **AND** the command prints only fork-owned current paths

#### Scenario: Custom main branch analysis
- **WHEN** the user runs `forklift files --main-branch dev`
- **THEN** the command analyzes `dev` and `upstream/dev`
- **AND** all ownership semantics remain the same

### Requirement: Files command uses local refs only
The command SHALL use local branch and remote-tracking refs already present in the repository. It MUST NOT fetch remotes as part of ownership analysis.

#### Scenario: Local refs are present
- **WHEN** the selected local branch and `upstream/<main-branch>` both exist locally
- **THEN** the command computes ownership from those refs without network access

#### Scenario: Required local upstream ref is missing
- **WHEN** `upstream/<main-branch>` is missing locally
- **THEN** the command exits non-zero with a clear ref-resolution error
- **AND** no owned-file output is printed

### Requirement: Ownership SHALL use current-path diff semantics
The command SHALL determine ownership from the diff between `upstream/<main-branch>` and `<main-branch>` using current-path semantics. Adds, renames, and copies MUST be interpreted by their current destination path.

#### Scenario: Added file is fork-owned
- **WHEN** a path exists on `<main-branch>` and no path with that current name exists on `upstream/<main-branch>`
- **THEN** the command includes that path in the output

#### Scenario: Renamed file is fork-owned by current path
- **WHEN** the fork renames a path to `fork/new_name.py` and `fork/new_name.py` is absent from `upstream/<main-branch>`
- **THEN** the command includes `fork/new_name.py`
- **AND** the command does not report only the old path name

#### Scenario: Copied file is fork-owned by current path
- **WHEN** the fork copies `src/base.py` to `fork/custom_base.py` and `fork/custom_base.py` is absent from `upstream/<main-branch>`
- **THEN** the command includes `fork/custom_base.py`

#### Scenario: Shared path is not fork-owned
- **WHEN** both upstream and the fork introduced the same current path after the merge base
- **THEN** the command does not report that path as fork-owned

### Requirement: Files output SHALL ignore working tree state
The command SHALL consider committed branch history only. It MUST ignore staged, unstaged, and untracked working tree files.

#### Scenario: Uncommitted file exists locally
- **WHEN** the working tree contains an untracked or modified path that is absent from committed branch history
- **THEN** that path does not appear in `forklift files` output

### Requirement: Files output SHALL be alphabetized plain text
Successful command output SHALL be sorted alphabetically by path and rendered as headerless plain text.

#### Scenario: Default output
- **WHEN** fork-owned files exist and the user runs `forklift files`
- **THEN** the command prints one path per line in alphabetical order
- **AND** no headers or markdown tables are printed

#### Scenario: Empty ownership set
- **WHEN** no fork-owned paths exist for the analyzed branch pair
- **THEN** the command prints exactly `No fork-owned files.`

### Requirement: Optional hash output SHALL report current-path introduction commits
When the user passes `--hash`, the command SHALL print a second column containing the short commit hash where the current path first appeared in `merge-base..<main-branch>`. The hash refers to the current path name, not to rename ancestry before that path existed.

#### Scenario: Hash output for added file
- **WHEN** the user runs `forklift files --hash` and a fork-owned added file exists
- **THEN** the command prints `path<TAB>shortsha` for that path

#### Scenario: Hash output for renamed file
- **WHEN** a fork-owned path entered the fork side via rename
- **THEN** the printed short hash corresponds to the rename commit where the current path first appeared
- **AND** the command does not chase the previous path name further back in history

#### Scenario: Hash output for copied file
- **WHEN** a fork-owned path entered the fork side via copy
- **THEN** the printed short hash corresponds to the copy commit where the current path first appeared

### Requirement: Files command SHALL remain outside orchestration lifecycle
`forklift files` SHALL be a read-only host-side inspection command. It MUST NOT create run directories, update run-state files, launch containers, rewrite commits, or publish local review branches.

#### Scenario: Local repository state preserved
- **WHEN** the user runs `forklift files` in a clean repository
- **THEN** tracked-file status remains unchanged after command completion
- **AND** no run directory or publication branch is created
