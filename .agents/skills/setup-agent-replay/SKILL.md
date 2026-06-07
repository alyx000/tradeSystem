---
name: setup-agent-replay
description: Set up a local agent replay server for Raindrop Workshop. Use when the user wants Workshop to replay a captured trace against their real local agent code and tools. Creates/updates `.raindrop/agents.yaml`, scaffolds a language-appropriate replay server, registers the project with `raindrop replay register`, and verifies `/health`.
---

You are running in the user's agent repository, not in Workshop.

Your job is to make the agent replayable from Raindrop Workshop without the user manually starting a replay server.

## Target Contract

Workshop expects:

- `.raindrop/agents.yaml` committed in the agent repo.
- A replay server command in that yaml, plus `cwd` when the command must run from a subdirectory.
- A replay server with:
  - `GET /health`
  - `POST /replay`
- A local project registration via `raindrop replay register`.

Replay server ports must be in `61020-61044`.

Workshop runs on `http://localhost:5899`.

If Raindrop MCP is not available or cannot reach Workshop, run:

```bash
raindrop workshop
```

Then retry the MCP/tool call. Do not stop just because the MCP server is unavailable.

## If `.raindrop/agents.yaml` Already Exists

Before changing anything, read `.raindrop/agents.yaml`.

Ask the user whether to:

1. Start/register the existing replay setup.
2. Add a new agent replay entry.

If they choose start/register:

1. Run the configured command if needed.
2. Verify `GET /health`.
3. Run `raindrop replay register`.
4. Stop. Do not scaffold a duplicate server.

## Setup Steps

### 1. Identify The Agent

Find:

- Event name used by tracing (`eventMetadata({ eventName: ... })`, equivalent SDK call, or current Workshop run).
- Agent entry point to invoke.
- Runtime context the agent requires, such as `orgId`, `orgPublicId`, `convoId`, `userId`, `source`.
- Model defaults and obvious supported model overrides.
- Existing script/package manager conventions.

If the agent is not instrumented with Raindrop/Workshop tracing, stop and tell the user to instrument it first.

### 2. Infer Input And Prefill

Create:

```yaml
input:
  orgPublicId: string
  orgId: number

prefillFromTrace:
  orgPublicId: properties.orgPublicId
  orgId: properties.orgId
```

`input` is the shape passed to the replay server as `request.context`.

`prefillFromTrace` tells Workshop how to prefill that context from the selected trace. The user may edit the values in the UI before replay.

Only include fields the agent actually needs to run.

### 3. Pick A Port

Pick the first unused port in `61020-61044`.

Use that port as a constant in the generated replay server code:

```typescript
const PORT = 61020;
```

Do not put the port in `.raindrop/agents.yaml`.

### 4. Generate The Replay Server

Create the smallest server that fits the project language and conventions.

Required `GET /health` response:

```json
{
  "ok": true,
  "eventName": "triage-agent-dev",
  "port": 61020,
  "cwd": "/absolute/path/to/project-or-subpackage",
  "command": "pnpm replay-server",
  "input": {
    "orgPublicId": "string",
    "orgId": "number"
  },
  "prefillFromTrace": {
    "orgPublicId": "properties.orgPublicId",
    "orgId": "properties.orgId"
  },
  "models": ["claude-sonnet-4-20250514", "gpt-4.1"]
}
```

Required `POST /replay` request:

```typescript
interface ReplayRequest {
  replayRunId: string;
  sourceRunId?: string;
  messages: Message[];
  systemPrompt?: string;
  userMessage?: string;
  model?: string;
  context: Record<string, unknown>;
}
```

The server should keep the `POST /replay` request open until the replayed agent run finishes or fails. Workshop's `replay_run` MCP tool waits on this single request and surfaces its success or failure to the calling agent.

Successful response:

```json
{ "replayId": "abc123", "status": "done" }
```

Failure response:

```json
{ "status": "error", "message": "Failed to create turn: ...", "stack": "..." }
```

Use a non-2xx HTTP response for request/agent failures when possible. A 200 response with `status: "error"` is also accepted and will be surfaced through the same MCP tool. Do not start the agent in a fire-and-forget async task that only logs errors after `POST /replay` has returned; Workshop cannot observe those failures.

Replay should exercise the agent with as few side effects as possible. Before wiring the replay endpoint, inspect the agent's production entrypoint and extract the smallest reusable agent-running function you can. Prefer dependency injection, test doubles, dry-run flags, transaction rollback, or no-op adapters so replay avoids writes to the user's application database, billing systems, queues, email/SMS providers, analytics, or other external services. Preserve the real LLM/tool behavior needed for a faithful trace, but do not blindly call a production request handler if it persists application state as part of normal operation.

Before invoking the agent, set:

```typescript
process.env.RAINDROP_LOCAL_DEBUGGER = "http://localhost:5899/v1/";
```

Do not pass `replayRunId` through an environment variable.

Pass `request.context`, `request.messages`, `request.systemPrompt`, `request.userMessage`, and `request.model` into the agent in the way that matches the local codebase.

For trace stitching, pass `request.replayRunId` through the SDK metadata surface:

1. Prefer the SDK's event id field. Workshop uses this as the primary merge key.
2. If the SDK cannot set an event id, add `replayRunId: request.replayRunId` to metadata properties.

Make sure both the original run and the replay run have the same event name.

Use the correct field name for the language:

| SDK | Preferred stitch field |
|---|---|
| JS AI SDK | `eventMetadata({ eventId: request.replayRunId, ... })` |
| JS Claude Agent SDK | `eventMetadata({ eventId: request.replayRunId, ... })` |
| Python | `begin(event_id=request.replayRunId, ...)` / `Interaction(event_id=request.replayRunId, ...)` |
| Go | `SpanOptions{EventID: request.replayRunId}` or an interaction started with that event id |
| Rust | `SpanOptions { event_id: request.replayRunId, ... }` or an interaction started with that event id |

etc. There are many more SDKs supported.

Examples:

```typescript
// AI SDK
experimental_telemetry: {
  isEnabled: true,
  metadata: eventMetadata({
    eventId: request.replayRunId,
    eventName: "<event-name>",
    properties: {
      ...request.context,
    },
  }),
}

// Claude Agent SDK
eventMetadata({
  userId: "<user-id>",
  eventId: request.replayRunId,
  eventName: "<event-name>",
  properties: {
    ...request.context,
  },
})
```

### 5. Standard Message Format

Workshop sends AI SDK-style JSON:

```typescript
interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string | ContentPart[];
  toolCalls?: ToolCall[];
  toolCallId?: string;
}
```

For TypeScript AI SDK agents, this usually passes through directly.

For other SDKs or languages, generate the small adapter needed by that project.

### 6. Write `.raindrop/agents.yaml`

Example:

```yaml
triage-agent-dev:
  # Optional. Relative paths are resolved from the project root that
  # contains `.raindrop/agents.yaml`.
  cwd: apps/dawn
  command: pnpm replay-server

  input:
    orgPublicId: string
    orgId: number

  prefillFromTrace:
    orgPublicId: properties.orgPublicId
    orgId: properties.orgId

  models:
    - claude-sonnet-4-20250514
    - gpt-4.1
```

If adding another agent, preserve existing entries.

Use `cwd` for monorepos or nested apps when the replay command is only valid from a package directory. For example, prefer:

```yaml
triage-agent-dev:
  cwd: apps/dawn
  command: pnpm replay-server
```

over registering a command from the repository root that only works inside `apps/dawn`.

### 7. Add Scripts

Add the replay command using the project's normal conventions.

Examples:

- TypeScript: `pnpm replay-server`
- Python: `python scripts/replay_server.py` or `poetry run python scripts/replay_server.py`
- Go: `go run ./cmd/replay-server`
- Rust: `cargo run --bin replay-server`

If the project has a normal dev command, add or suggest a combined command (`dev:all`, `dev:replay`, `make dev`, etc.) when it is safe and idiomatic.

### 8. Verify

Start the replay server once.

Check:

```bash
(cd <configured-cwd> && <configured-command>)
curl -fsS http://127.0.0.1:<port>/health
```

Verify the response includes:

- `ok: true`
- `eventName`
- `port`
- `cwd`
- `command`
- `input`
- `prefillFromTrace`

### 9. Register

Run from the project root:

```bash
raindrop replay register
```

Registration starts each configured command from its configured `cwd`, waits for `/health`, confirms the agent is reachable, and stores the last seen port. If registration fails, fix `.raindrop/agents.yaml` instead of continuing.

Require a successful confirmation like:

```text
Registered replay project:
  path: /path/to/project
  config: /path/to/project/.raindrop/agents.yaml
  agents:
    - eventName: triage-agent-dev
```

If registration fails, stop and show the error. Do not claim replay is ready.

### 10. Run A Test Replay

Ask the user before running the final test replay.

Default to replaying a past trace. Use the current Workshop run when available; otherwise ask the user to select a source run in Workshop or provide a run id. Do not invent a synthetic replay unless the user explicitly asks for one.

Ask with a concrete default:

> "Replay setup is registered. Should I run a test replay now using the selected/past Workshop trace?"

If the user agrees, prefer the Raindrop MCP `replay_run` tool if it is available. If Raindrop MCP is unavailable, run `raindrop workshop`, then retry MCP. If MCP still is not available, trigger Workshop's replay API directly:

```bash
curl -N -fsS \
  -H "Content-Type: application/json" \
  -d '{"runId":"<source-run-id>"}' \
  http://127.0.0.1:5899/api/replay
```

Verify all of the following:

- Workshop auto-started or reached the replay server.
- The replay server accepted `POST /replay`.
- A replay run id was returned.
- The replay emitted traces back to Workshop.
- You can see the replay trace in Workshop, either by MCP/querying the run or by opening it in the UI.

If the replay returns `missing_replay_agent` or says to run `/setup-agent-replay`, fix registration/startup and retry. If the replay server starts but no traces appear, fix `RAINDROP_LOCAL_DEBUGGER` and SDK metadata stitching before stopping.

## Output

When done, tell the user:

- Which event name was registered.
- Which port the replay server uses.
- Which command Workshop will run.
- That Workshop can now start the replay server automatically when Replay is clicked.
- The replay run id from the test replay and how you verified its traces appeared.

Do not introduce readonly mode, mocked tools, or mutating-tool classifications.
