# Forklift: Automated Fork Maintenance System

## Overview

Forklift is an automated system for maintaining forks of open source projects. It leverages coding agents (OpenCode or oh-my-pi) to handle routine maintenance tasks like merging upstream changes, resolving simple conflicts, and running tests, while escalating complex decisions to the maintainer via Telegram notifications.

## Core Concepts

### The Problem
- Developers create numerous forks for features, bug fixes, and experiments
- Keeping forks synchronized with upstream requires constant manual intervention
- Merge conflicts accumulate over time, making updates increasingly difficult
- Important upstream security fixes and improvements get missed

### The Solution
Forklift automates the fork maintenance lifecycle by:
1. Monitoring upstream repositories for changes
2. Attempting automatic merges on a configurable schedule
3. Using AI coding agents to resolve simple conflicts
4. Notifying maintainers when human judgment is required or via periodic state digests
5. Processing maintainer responses through a bidirectional communication system

<comment>
I think that saying a human is _only_ notified when human judgement is required isn't quite right. I think I'd appreciate an email digest every few days (configurable) telling me the state of things.
</comment>

## System Architecture

### Components

#### 1. Fork Registry
- SQLite database tracking:
  - Fork metadata (upstream URL, fork URL, branch mappings)
  - Maintenance schedule (cron expressions)
  - Notification preferences
  - Historical merge results
  - Current fork health status

<comment>
So...I'm ok with this, but I would want there to be a tool that can pull this data in by pointing it at a repo on disk, followed by a little interactive Q&A to collect the settings from me.
</comment>

#### 2. Scheduler Service
- Cron-based task scheduler
- Manages work queue for merge attempts
- Handles retry logic with exponential backoff
- Tracks execution time budgets per fork

<comment>
Just thinking out loud here, but do we need a work queue? Why not just wake up an agent on the schedule, and let it run? I have the compute, and there won't be that many forks.
</comment>

#### 3. Agent Harness
- Interfaces with OpenCode or oh-my-pi
- Provides sandboxed Git environment
- Enforces execution time limits
- Captures agent decisions for audit trail

<comment>
More than just a Git environment -- they'll have a dev environment. They'll need to be able to build the project, run tests, potentially run the software themselves, etc
</comment>

#### 4. Notification Bridge
- Telegram bot for outbound notifications
- Home Assistant webhook receiver for replies
- Message routing and context preservation
- Rich message formatting with diff previews

<comment>
No inline diffs. Code-level decisions will never be made in Telegram. 

Some decisions will be: yes or nos, this or that. The questions themselves can be a decent length, but the answers are short.

And it shouldn't be a requirement that Telegram is used, even for those short answers. A link, or a session identifier, SOMETHING that can be used to resume a chat on a desktop computer, will need to accompany the message. The process should also be considered very async -- potentially hours or days before an answer arrives.
</comment>

#### 5. Container Sandbox
- Docker-based isolation per merge attempt
- Resource limits (CPU, memory, disk, network)
- Ephemeral workspaces
- Security-hardened base image

## Workflow

### 1. Fork Registration
```yaml
# Example fork configuration
fork:
  name: "my-project-fork"
  upstream: "https://github.com/original/project"
  fork: "https://github.com/myuser/project"
  branches:
    - upstream: "main"
      fork: "main"
      strategy: "rebase"
  schedule: "0 3 * * MON"  # Weekly on Monday at 3 AM
  time_budget: 1800  # 30 minutes max
  notify:
    telegram_chat_id: "@username"
    on_success: false
    on_conflict: true
    on_failure: true
```

<comment>
I'd like to see some of these fields as optional, defaulting to a user-level config, which itself defaults to a set of reasonable defaults where appropriate
</comment>

### 2. Scheduled Merge Attempt
1. Scheduler triggers merge job
2. Sandbox container spawned with:
   - Cloned fork repository
   - Configured Git identity
   - Agent harness installed
3. Agent attempts merge:
   - Fetches upstream changes
   - Creates feature branch
   - Attempts merge/rebase
   - Runs test suite if configured
   - Analyzes conflicts if present

<comment>
I think this seems OK. The git identity wouldn't have permissions to push or force push main. The result of the merge/rebase would be a PR.
</comment>

### 3. Conflict Resolution Flow
When conflicts arise, the agent:
1. Categorizes conflicts by type:
   - Whitespace/formatting only
   - Simple semantic conflicts
   - Complex logic conflicts
   - File renames/moves
2. Attempts resolution for simple cases
3. Generates conflict report for complex cases
4. Prepares notification with context

<comment>
tiny nit: The top level forklift: is unnecessary. Let's get rid of it
</comment>

### 4. Human-in-the-Loop Decision
```
📋 Forklift: Merge Conflict in my-project-fork

Upstream has 12 new commits. I resolved 3 conflicts automatically but need help with:

🔧 src/api/handler.ts
  - Lines 45-52: New error handling conflicts with your custom logic
  - Your version adds retry logic
  - Upstream adds circuit breaker
  
💡 Suggested resolution: Combine both approaches

Reply with:
- "accept upstream" - Take their changes
- "keep mine" - Keep your version  
- "combine" - Let me attempt merging both
- "skip" - Postpone this merge
```
```

### 5. Response Processing
- Home Assistant receives Telegram reply
- Routes response back to waiting agent
- Agent applies decision and continues
- Results logged and next action determined

## Configuration

### Global Settings
```yaml
forklift:
  agent_provider: "oh-my-pi"  # or "opencode"
  default_time_budget: 1800
  max_retries: 3
  work_dir: "/var/forklift/workspaces"
  
  sandbox:
    image: "forklift:latest"
    memory_limit: "2G"
    cpu_limit: 2.0
    network_mode: "restricted"
    
  notifications:
    telegram_bot_token: "${TELEGRAM_BOT_TOKEN}"
    home_assistant_webhook: "https://ha.local/webhook/forklift"
```

### Per-Fork Settings
- Merge strategy (merge, rebase, squash)
- Files to always prefer (yours vs theirs)
- Custom agent instructions
- Notification thresholds
<comment>
Test command will be in AGENTS.md, and I think I'd like to introduce a repo-level context file for these other details: FORK.md

I'm not entirely clear on what a notification threshold is
</comment>

## Security Considerations

1. **Repository Access**
   - SSH keys stored encrypted
   - Per-fork access tokens with minimal scopes
   - No credentials in container environment

2. **Sandbox Isolation**
   - No network access except to Git remotes
   - Read-only mount of agent code
   - Dropped capabilities and syscall filtering
   - Non-root user execution

<comment>
Another possibility is that we pre-fetch the git repo imperatively

The problem with no network access is that repo setup becomes more difficult, so we'd have to script that for every one that needs it. Maybe not a huge deal, but it's another step that has to work. Worth considering.
</comment>

3. **Agent Constraints**
   - No ability to push directly to main branches
   - All changes go through PR/MR workflow
   - Audit log of all agent actions
   - Token-based usage limits

4. **Communication Security**
   - Telegram messages expire after 24 hours
   - Home Assistant webhook requires authentication
   - No sensitive data in notifications
   - Context tokens prevent replay attacks

<comment>
No message expiry is necessary. I don't consider any of this confidential, and my privacy is not at risk.

There should be nothing sensitive available to the bot, period.

HA needs auth, but we don't need to allow direct communication. Let's give them a domain socket, or a CLI that talks through one, or something. Validate everything coming through, from the other side.
</comment>

## Edge Cases and Error Handling

### Timeout Handling
- Graceful shutdown when approaching time budget
- Save partial progress for manual review
- Mark fork as "needs attention" in registry
- Notify with last successful action

### Network Failures
- Retry with exponential backoff
- Cache upstream refs between attempts
- Fall back to shallow clones for large repos
- Queue for next scheduled run if persistent

### Agent Failures
- Capture full agent transcript
- Automatic rollback to pre-merge state
- Escalate to human with failure context
- Temporary blacklist if repeated failures

<comment>
No need for automatic rollback if the only possible output for their repo changes is a PR
</comment>

### Concurrent Modifications
- Lock fork during maintenance
- Detect external pushes during merge
- Abort and reschedule if fork modified
- Optional: coordinate with CI/CD webhooks

<comment>
I wouldn't bother. Too much overhead...and these are forks for one person, not hundreds. Plus it's git...concurrency is exactly the problem we're already solving by handling rebases/merges. I vote to remove this section entirely
</comment>

## Monitoring and Observability

### Metrics
- Merge success/failure rates per fork
- Average time to complete merges
- Conflict resolution effectiveness
- Human intervention frequency
- Agent token usage and costs

<comment>
I like all of this, but what constitutes an effective resolution?
</comment>

### Health Checks
- Fork divergence metrics
- Days since last successful merge
- Pending security updates
- Test suite status
- License compatibility changes

### Dashboards
```yaml
Dashboard Views:
  - Overview: All forks status at a glance
  - Fork Detail: Individual fork history and health
  - Agent Performance: Success rates and resource usage
  - Cost Analysis: Token usage and compute costs
  - Intervention Log: Human decisions and outcomes
```

<comment>
Just give me a CLI instead
</comment>

## Implementation Phases

### Phase 1: Core Engine (MVP)
- Basic fork registry (file-based)
- Simple cron scheduler
- OpenCode/oh-my-pi integration
- Local Git operations only
- Console output for notifications

### Phase 2: Communication Layer
- Telegram bot integration
- Home Assistant webhook receiver
- Basic conflict categorization
- Structured notification format

### Phase 3: Intelligent Automation
- Conflict pattern learning
- Test suite integration
- Multi-branch support
- Dependency update handling

<comment>
I actually think test suite integration is a necessity for phase 1 to be considered a success
</comment>

### Phase 4: Production Readiness
- Web UI for configuration
- Comprehensive monitoring
- Multi-user support
- GitHub/GitLab/Bitbucket APIs
- Scheduled reporting

<comment>
eh, it'll never happen. let's not design it until we're there, and can see what we need
</comment>

## Example Use Cases

### 1. Security Patch Propagation
Upstream releases a critical security fix. Forklift:
- Detects the security-tagged commit
- Prioritizes immediate merge attempt
- Notifies on any conflicts
- Tracks patch application across all forks

<comment>
Sure. 
How does detection work, or is this part of the cron?
</comment>

### 2. Feature Branch Maintenance
Long-running feature branch needs regular updates:
- Weekly rebase against main
- Resolve simple conflicts automatically
- Maintain clean commit history
- Alert when manual intervention needed

### 3. Dependency Bumps
Upstream updates dependencies:
- Detect changes to package.json/go.mod/etc
- Run dependency audit
- Check for breaking changes
- Update lock files appropriately

<comment>
Just worth saying, I don't want to have to anticipate every situation the agent might find themselves having to deal with (I'm referring to dependency audit). They're smart, they'll understand. If we overspecify, they'll get all wrapped up in an instruction maze, instead of just doing what needs doing
</comment>

### 4. Documentation Syncing
Keep docs in sync while maintaining custom sections:
- Merge upstream documentation changes
- Preserve custom documentation additions
- Flag conflicts in technical content
- Auto-accept typo fixes

<comment>
Ignore docs ENTIRELY. Docs are not what we're doing here.
</comment>

## Agent Instructions Template

```markdown
You are maintaining a fork of {upstream_repo}. Your task is to merge changes from upstream into the fork while preserving the following customizations:

{fork_customizations}

Conflict Resolution Guidelines:
1. Preserve all custom features unless they directly conflict
2. Prefer upstream security fixes
3. Maintain API compatibility where possible
4. Run test suite after each resolution attempt
5. Document non-obvious merge decisions

<comment>
For each of these repos, the directives will _always_ be, preserve the custom features, and preserve upstream changes. For every single repo. If they can't, they come talk to me about it
</comment>

If you encounter conflicts you cannot resolve with confidence, prepare a detailed summary for human review including:
- What changed in upstream
- What customizations conflict
- Potential resolution strategies
- Risk assessment of each approach

<comment>
Yup, but conversational, and concise
</comment>
```

## Technical Implementation Details

### Git Strategy
```python
# Merge attempt workflow
def attempt_merge(fork_config):
    # 1. Create isolated worktree
    worktree = create_sandbox_worktree(fork_config.fork_url)
    
    # 2. Add upstream remote
    worktree.add_remote('upstream', fork_config.upstream_url)
    
    # 3. Fetch latest changes
    worktree.fetch('upstream', depth=fork_config.fetch_depth)
    
    # 4. Create merge branch
    branch_name = f'forklift/merge-{timestamp}'
    worktree.checkout(fork_config.base_branch)
    worktree.checkout_new_branch(branch_name)
    
    # 5. Attempt merge with strategy
    if fork_config.strategy == 'rebase':
        result = worktree.rebase(f'upstream/{fork_config.upstream_branch}')
    else:
        result = worktree.merge(f'upstream/{fork_config.upstream_branch}')
    
    return MergeAttempt(result, worktree, branch_name)
```

### Container Sandbox Dockerfile
```dockerfile
# forklift-sandbox.dockerfile
FROM ubuntu:24.04

# Install minimal Git and runtime
RUN apt-get update && apt-get install -y \
    git \
    openssh-client \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create sandboxed user
RUN useradd -m -s /bin/bash forklift && \
    mkdir -p /workspace /forklift && \
    chown forklift:forklift /workspace

# Install agent harness
COPY --chown=forklift:forklift ./harness /forklift/harness

# Security hardening
RUN chmod 700 /workspace && \
    chmod -R 755 /forklift

USER forklift
WORKDIR /workspace

# Entrypoint enforces time limits
ENTRYPOINT ["/forklift/harness/run.sh"]
```

### Message Queue Integration
```yaml
# Task queue message format
merge_task:
  id: "550e8400-e29b-41d4-a716-446655440000"
  fork_id: "my-project-fork"
  scheduled_time: "2024-02-13T03:00:00Z"
  attempt_number: 1
  time_budget: 1800
  context:
    last_successful_merge: "2024-02-06T03:00:00Z"
    pending_notifications: []
  
merge_result:
  task_id: "550e8400-e29b-41d4-a716-446655440000"
  status: "requires_human_decision"
  branch: "forklift/merge-20240213-030000"
  conflicts:
    - file: "src/api/handler.ts"
      type: "semantic"
      confidence: 0.3
  notification_sent: true
  expires_at: "2024-02-14T03:00:00Z"
```

### Home Assistant Integration
```yaml
# configuration.yaml
automation:
  - alias: "Forklift Telegram Reply Handler"
    trigger:
      - platform: event
        event_type: telegram_command
        event_data:
          command: '/forklift'
    action:
      - service: webhook.send
        data:
          webhook_id: forklift_response
          payload:
            chat_id: "{{ trigger.event.data.chat_id }}"
            message_id: "{{ trigger.event.data.message_id }}"
            text: "{{ trigger.event.data.args }}"
            context_token: "{{ trigger.event.data.context }}"
```

### Database Schema
```sql
-- Fork registry
CREATE TABLE forks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    upstream_url TEXT NOT NULL,
    fork_url TEXT NOT NULL,
    config JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Merge history
CREATE TABLE merge_attempts (
    id TEXT PRIMARY KEY,
    fork_id TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT NOT NULL,
    branch_name TEXT,
    upstream_sha TEXT,
    agent_transcript TEXT,
    human_decision TEXT,
    FOREIGN KEY (fork_id) REFERENCES forks(id)
);

-- Notification queue
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    merge_attempt_id TEXT NOT NULL,
    sent_at TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    context_token TEXT NOT NULL,
    response_received_at TIMESTAMP,
    response_text TEXT,
    FOREIGN KEY (merge_attempt_id) REFERENCES merge_attempts(id)
);
```

## Quick Start Guide

### 1. Install Forklift
```bash
# Clone repository
git clone https://github.com/yourusername/forklift
cd forklift

# Install dependencies
pip install -e .

# Initialize database
forklift init
```

### 2. Configure Your First Fork
```bash
# Add a fork to monitor
forklift add-fork \
  --name "my-awesome-fork" \
  --upstream "https://github.com/original/project" \
  --fork "https://github.com/myuser/project" \
  --schedule "0 3 * * MON" \
  --notify telegram:@myusername
```

### 3. Set Up Communication
```bash
# Configure Telegram bot
forklift config set telegram.bot_token "YOUR_BOT_TOKEN"

# Set up Home Assistant webhook
forklift config set homeassistant.webhook_url "https://your-ha.local/webhook/forklift"
forklift config set homeassistant.token "YOUR_LONG_LIVED_TOKEN"
```

### 4. Test Run
```bash
# Dry run to test configuration
forklift test my-awesome-fork --dry-run

# Run immediately (bypass schedule)
forklift run my-awesome-fork --interactive
```

### 5. Start Scheduler
```bash
# Run in foreground for testing
forklift scheduler start

# Or run as systemd service
sudo systemctl enable forklift
sudo systemctl start forklift
```

## Future Enhancements

1. **Multi-Repository Patterns**
   - Fork families that should stay synchronized
   - Cross-repository dependency tracking
   - Coordinated merge strategies

2. **Advanced Conflict Resolution**
   - ML-based conflict prediction
   - Historical decision pattern matching
   - Semantic diff understanding

3. **Integration Ecosystem**
   - GitHub Actions workflow triggers
   - Slack/Discord notifications
   - JIRA ticket creation for conflicts
   - Prometheus metrics export

4. **Collaborative Features**
   - Team approval workflows
   - Merge preview sharing
   - Conflict resolution voting
   - Knowledge base building

5. **Performance Optimizations**
   - Distributed agent workers
   - Incremental fetch strategies
   - Binary diff caching
   - Parallel conflict analysis

## Real-World Example Scenarios

### Scenario 1: Vendored Dependencies Fork
You maintain a fork of a project that vendors its dependencies, while upstream uses package managers:

```yaml
fork:
  name: "offline-friendly-tool"
  customizations:
    - "All dependencies vendored in /vendor directory"
    - "No network access required for builds"
  merge_rules:
    - ignore: ["package-lock.json", "go.sum"]
    - run_after_merge: "make vendor-deps"
  agent_context: |
    When upstream updates dependencies, vendor them:
    1. Apply upstream changes to package files
    2. Run vendoring script
    3. Commit both package and vendor changes
```

### Scenario 2: Enterprise Security Fork
Corporate fork with additional security controls:

```yaml
fork:
  name: "webapp-enterprise"
  customizations:
    - "SAML authentication added"
    - "Audit logging for all API calls"
    - "Removed social media integrations"
  conflict_preferences:
    - file: "src/auth/*"
      strategy: "always_keep_mine"
    - file: "src/api/audit.ts"
      strategy: "must_preserve"
  test_commands:
    - "npm test"
    - "npm run test:security"
    - "npm run test:compliance"
```

### Scenario 3: Platform-Specific Fork
Fork that adds platform-specific features:

```yaml
fork:
  name: "mobile-app-android-tv"
  customizations:
    - "Android TV navigation support"
    - "Remote control input handling"
    - "10-foot UI modifications"
  merge_strategy:
    - "Cherry-pick security fixes only"
    - "Skip UI updates from mobile-focused upstream"
  agent_context: |
    Upstream is mobile-first. Preserve our TV-specific:
    1. Navigation must remain D-pad compatible
    2. Text must remain readable at distance
    3. Touch gestures should map to remote buttons
```

## Troubleshooting Guide

### Common Issues

1. **Agent Times Out During Merge**
   - Increase time_budget for complex forks
   - Enable shallow cloning for large repositories
   - Consider splitting into multiple smaller merges

2. **Conflicts Keep Recurring**
   - Add explicit merge rules for problematic files
   - Consider restructuring fork to minimize conflicts
   - Document why conflicts occur in agent context

3. **Test Suite Failures After Merge**
   - Add pre-merge test run to catch issues early
   - Configure agent to run incremental tests
   - Set up test environment in sandbox

4. **Notification Delivery Issues**
   - Verify Telegram bot has message permissions
   - Check Home Assistant webhook logs
   - Ensure network connectivity from container

### Debug Mode
```bash
# Enable debug logging
forklift --debug run my-fork

# Keep sandbox container for inspection
forklift run my-fork --keep-sandbox

# View agent conversation transcript
forklift logs --transcript merge-attempt-id

# Test notification pipeline
forklift test-notify --mock-conflict
```

## Security Hardening Checklist

- [ ] Encrypt Git credentials at rest
- [ ] Use separate SSH keys per fork
- [ ] Enable container security scanning
- [ ] Implement rate limiting for API calls
- [ ] Audit agent permissions regularly
- [ ] Rotate webhook tokens periodically
- [ ] Monitor for suspicious merge patterns
- [ ] Backup fork configurations
- [ ] Test disaster recovery procedures

## Summary

Forklift transforms fork maintenance from a manual chore into an intelligent, automated process. By combining the power of AI coding agents with battle-tested Git workflows, it enables developers to maintain multiple forks without the constant overhead of manual merges.

Key innovations:
- **Intelligent Automation**: AI agents handle routine conflicts, escalating only when human judgment is needed
- **Asynchronous Workflow**: Notifications via Telegram allow decisions at your convenience, not the tool's schedule
- **Security First**: Every operation runs in an isolated sandbox with strict limits
- **Extensible Design**: Plugin architecture supports new agent providers and communication channels

Whether you're maintaining enterprise forks with compliance requirements, platform-specific variants, or simply keeping feature branches updated, Forklift ensures your forks stay current with minimal manual intervention.

The system respects that your time is valuable—it handles what it can, asks when it must, and always provides clear context for decisions. No more discovering months later that your fork is hopelessly behind upstream. No more spending weekends resolving massive conflict sets. Just reliable, intelligent fork maintenance that works while you focus on building.

---

*Next Steps: Begin with Phase 1 implementation focusing on the core merge engine and basic agent integration. Once proven with local repositories, expand to include the communication layer and production sandboxing.*
