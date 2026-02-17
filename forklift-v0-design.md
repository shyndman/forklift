# Forklift v0 Design Document

## Philosophy

Forklift v0 embraces radical simplicity through maximum trust in AI agents. Rather than building elaborate systems to handle edge cases, we let the agent figure things out, intervening only when it explicitly asks for help.

Core principles:
- Convention over configuration
- Filesystem as database
- Hard time limits over complex resource management
- Trust the agent to find solutions
- No premature optimization

## System Overview

Forklift is a tool that automatically merges upstream changes into your fork daily. It runs locally, creates pull requests, and asks for help when stuck.

The entire system consists of:
1. A Python orchestrator script
2. A Docker container with development tools
3. Git conventions (origin = fork, upstream = source)
4. Filesystem-based state management

## How It Works

### Invocation

The user navigates to their fork directory and runs:
```
forklift
```

No arguments. No configuration files. The tool infers everything from Git remotes.

### Git Conventions

Forklift assumes standard Git remote naming:
- `origin` points to your fork
- `upstream` points to the original repository

These remotes must exist before running forklift. This is already standard practice for most developers working with forks.

### The 8-Minute Window

Each merge attempt gets exactly 8 minutes. This hard limit:
- Forces quick decision-making
- Prevents runaway processes
- Enables multiple runs per day without excessive cost
- Provides fast feedback on what works

If the agent cannot complete the merge in 8 minutes, it writes a STUCK.md file explaining what it needs.

### Isolation Strategy

The agent runs in a Docker container with:
- No Git remotes configured (prevents any push attempts)
- No SSH keys or authentication tokens
- No access to local network resources
- No knowledge of user identity or organization
- Full internet access for package downloads

The host system fetches the latest commits and prepares a clean workspace with:
- Current `main` branch from origin
- Current `main` branch from upstream (as `upstream/main`)
- All source files and Git history
- No remote configurations

### Container Environment

A single "kitchen sink" container includes:
- Docker build context in `docker/kitchen-sink/` producing the `forklift/kitchen-sink:latest` image
- Base image `ubuntu:24.04` (jammy)
- System packages: `git`, `build-essential`, `cmake`, `pkg-config`, `python3`, `python3-venv`, `python3-pip`, `curl`, `wget`, `unzip`, `ca-certificates`, `openssl`, `libssl-dev`
- Language runtimes: Python 3, Rust via rustup, Node.js via `n`, Bun installer, PyEnv for version pinning
- Tooling: jq, ripgrep, fd, tree, make, bash-completion, other standard CLI helpers
- Harness bits copied into `/opt/forklift/harness` with entrypoint `/opt/forklift/harness/run.sh`
- Default bind mounts: `/workspace` (run workspace) and `/harness-state` (agent logs/state), both read-write
- Container user `forklift` (UID 1000, GID 1000) owns `/workspace`; host orchestration must ensure the mounted directories are writable by that UID (e.g., via `chown -R 1000:1000` after cloning)
The harness writes its rendered instructions to `/harness-state/instructions.txt`, echoes any detected FORK.md content, and appends an "Agent Command" section whenever we run additional commands via `FORKLIFT_DOCKER_COMMAND`. This keeps every run self-documenting and lets us capture verification artifacts straight from the container logs.






This eliminates project-type detection complexity. The agent determines which tools to use based on project files.
At runtime the orchestrator binds `runs/<project>_<timestamp>/workspace` → `/workspace` and `runs/<project>_<timestamp>/harness-state` → `/harness-state`, both read-write. No other host paths are exposed.


### Directory Structure

Forklift maintains a simple directory structure:
```
~/.local/state/forklift/
├── runs/
│   └── projectname_2024-02-13_080456/
│       ├── workspace/          # Copied repository
│       ├── harness-state/      # Agent logs and state
│       └── STUCK.md           # Only if agent needs help
└── forklift.py                # The orchestrator
```

Each run gets a timestamped directory. Old runs can be cleaned up manually or via a simple age-based policy.
Each run directory also includes `metadata.json` plus any harness outputs (instructions, fork context, ad-hoc command logs). The host never mutates those files after the container exits, so operators can archive them as immutable provenance.



### The Orchestration Process

When invoked, the orchestrator:

1. **Fetches latest commits** from both origin and upstream remotes
2. **Creates run directory** named `<project>_<YYYYMMDD_HHMMSS>` (project prefix before timestamp)
3. **Duplicates repository** into the run workspace directory
4. **Removes Git remotes** from the duplicated repository
5. **Starts Docker container** with:
   - Workspace volume mounted read-write
   - Harness state directory volume mounted
   - Network isolation except internet access
   - 8-minute timeout enforced externally
6. **Waits for timeout** or container exit
7. **Terminates container** if still running at timeout
8. **Verifies integration** by checking if upstream commits exist in main
9. **Creates pull request** if verification passes and changes exist

### Agent Instructions

The agent receives simple, trust-based instructions:
- Merge upstream/main into main
- Run any tests you can find
- Create meaningful commit messages
- If you get stuck, write STUCK.md explaining what you need
- You have 8 minutes

The agent is expected to:
- Identify the project type and test commands
- Attempt the merge or rebase
- Resolve simple conflicts
- Run basic verification
- Recognize when human judgment is needed

### Context via FORK.md

Users can optionally create a FORK.md file in their repository root to provide context:
```
This fork adds offline support by vendoring all dependencies in /vendor.
When merging upstream changes, preserve vendor directory contents.
Our custom authentication in src/auth must be maintained.
```

The agent reads this file to understand the fork's purpose and special requirements.
During workspace preparation, the host copies `FORK.md` from the repo root into each workspace before remotes disappear. The harness mirrors that file to `/harness-state/fork-context.md` and prints it alongside the default instructions so both the agent and the operator share the same context snapshot.



### Verification

A simple check determines success:
- Are all upstream commits reachable from main?
- This works for both merge and rebase strategies
- The agent chooses the appropriate strategy

### Communication Model

v0 uses a filesystem-based communication model:
- Success: Changes appear in a pull request
- Stuck: STUCK.md file appears with questions
- Failed: Check harness-state/logs for details

No real-time notifications. The user checks results when convenient.
Because the harness logs instructions, FORK.md content, and any explicit commands inside `/harness-state`, maintainers can audit exactly what the agent saw without rerunning the container.




### STUCK.md Format

When the agent needs help, it writes a conversational file:
```
I tried to merge upstream/main but encountered conflicts in:
- src/auth/handler.ts (lines 45-67)
- src/api/routes.ts (lines 123-130)

The main issue is that upstream completely rewrote the authentication
flow, while your fork has custom OAuth handling for corporate SSO.

I need to know:
1. Should I keep your custom auth or adopt upstream's new approach?
2. If keeping yours, how should I integrate their security fixes?

The rest of the merge was straightforward - 47 files updated cleanly.
```

### Daily Automation

A simple cron entry runs forklift daily:
```
0 9 * * * cd ~/dev/my-fork && forklift
```

For multiple forks, a wrapper script can iterate through directories.

## What v0 Doesn't Have

Explicitly excluded from v0:
- Configuration files
- Database or persistent state
- Real-time notifications  
- Web interface
- Multiple fork management UI
- Retry logic
- Complex scheduling
- Container customization
- Response processing
- Metrics dashboards

## Success Metrics

v0 succeeds if:
- 50% or more of daily merges complete without intervention
- STUCK.md files clearly communicate what's needed
- Harness logs capture default instructions, fork context, and the executed command for every run.

- The 8-minute timeout is sufficient for simple merges
- PRs created are merge-ready
- The system runs reliably from cron

## Evolution Path

After v0 proves the concept:
- Add Telegram notifications for STUCK.md files
- Implement response processing for simple yes/no questions
- Extend timeout for complex projects
- Add metrics collection and analysis
- Support multiple forks in single invocation

But v0 ships without any of these. Maximum learning from minimum complexity.

## Implementation Notes

The orchestrator should be a single Python file under 500 lines. It uses only standard library plus Docker SDK. No frameworks, no databases, no message queues.

The container builds once and rarely changes. It's intentionally oversized to avoid project-specific customization.

The agent harness configuration focuses on giving the agent maximum context about its task while preventing any destructive actions through isolation rather than instruction.

## Summary

Forklift v0 is radically simple:
- One command: `forklift`
- One timeout: 8 minutes
- One container: kitchen sink of tools
- One communication method: filesystem
- One goal: merge upstream and create PR

By trusting the agent and accepting that some attempts will need human help, we can build something useful immediately and learn what additional complexity is actually needed through real usage.