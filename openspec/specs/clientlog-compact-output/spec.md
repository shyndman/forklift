# clientlog-compact-output Specification

## Purpose
TBD - created by archiving change compact-clientlog-output. Update Purpose after archive.
## Requirements
### Requirement: Compact clientlog output is tool-and-message first
`forklift clientlog <run-id>` SHALL render transcript output in a compact, signal-first format where tool calls and message text are the primary visible content.

#### Scenario: Tool call with command and output
- **WHEN** a parsed event is `tool_use` and includes tool input plus output text
- **THEN** output SHALL include, in readable form, the tool name, input arguments, and response/output body

#### Scenario: Text-bearing event
- **WHEN** a parsed event contains message text
- **THEN** output SHALL include that text directly as message content

### Requirement: Compact output suppresses protocol internals
The compact renderer SHALL suppress low-signal protocol internals from user-visible output, including `messageID`, `part.id`, `callID`, `snapshot`, and token/cost accounting payloads.

#### Scenario: Tool event contains part and call identifiers
- **WHEN** a `tool_use` event includes `part.id` and `callID`
- **THEN** those identifiers SHALL NOT be printed in the compact output

#### Scenario: Step finish contains token accounting
- **WHEN** a `step_finish` event includes token/cost metadata
- **THEN** compact output SHALL NOT print the token/cost blob

### Requirement: Non-success tool execution is clearly visible
When a tool event indicates non-success status, compact output SHALL surface that status clearly in the rendered block.

#### Scenario: Failed tool invocation
- **WHEN** a `tool_use` event reports `state.status = "failed"`
- **THEN** the compact tool block SHALL include an explicit status line indicating failure

### Requirement: Unknown events remain visible without raw fallback dumps
For JSON events that are not explicitly formatted as tool or message output, the renderer SHALL emit a concise generic event line instead of pretty-printing raw payload JSON.

#### Scenario: Unrecognized JSON event type
- **WHEN** the renderer receives a JSON event type without a dedicated compact formatter
- **THEN** output SHALL include a concise `EVENT <type>` representation and SHALL NOT dump full raw JSON payload text

### Requirement: Clientlog output is hard-cutover to compact mode
The `clientlog` command SHALL use compact formatting as the only transcript presentation mode and MUST NOT provide a compatibility path that reproduces the prior step-box/metadata-heavy output.

#### Scenario: Default invocation
- **WHEN** the user runs `forklift clientlog <run-id>`
- **THEN** output SHALL be compact and SHALL NOT contain legacy step-box headings

#### Scenario: Follow invocation
- **WHEN** the user runs `forklift clientlog <run-id> --follow`
- **THEN** streamed output SHALL follow the same compact contract used by snapshot mode

### Requirement: Compact output does not annotate completeness state
Compact rendering SHALL emit available events as-is and SHALL NOT add synthetic completeness markers such as `open`, `incomplete`, or `pending-group`.

#### Scenario: Snapshot ends before a matching lifecycle companion event
- **WHEN** a snapshot contains early events from a logical group but not later companion events
- **THEN** output SHALL render only available compact events and SHALL NOT add completeness annotations

