# Architecture

Long Novel Agent Kit is a local state and protocol layer for long-form fiction.

## Components

```text
Desktop Agent
  | uses native model, parsing, retrieval, and long context
  v
MCP server or CLI
  | validates requests and writes guarded state
  v
.novel-agent/
  | durable continuity state
  v
Next chapter context, checks, handoff, and proposal review
```

## State Folder

Every novel project keeps durable state in `.novel-agent/`.

Important files:

- `manifest.json`: project metadata and schema version
- `rules.json`: required phrases, forbidden phrases, future markers, rename bans
- `chapters.jsonl`: accepted chapter metadata and tails
- `facts.jsonl`: structured continuity facts
- `sources.jsonl`: accepted source summaries
- `research.jsonl`: external research notes
- `conflicts.jsonl`: resolved contradictions
- `characters.json`: character state and relationships
- `debts.json`: foreshadowing and plot debts
- `contracts.jsonl`: chapter goals and acceptance checks
- `proposals.jsonl`: post-write update proposals
- `audit.jsonl`: durable write audit log
- `snapshots/`: rollback snapshots

## Read Path

`prepare-session` builds a chapter startup packet:

- project health
- handoff report
- recent agent activity
- open continuity threads
- chapter-safe context
- context fingerprint
- state fingerprint
- tool gates

The desktop agent should draft from that packet, not from chat history alone.

## Write Path

Durable writes are guarded:

1. The author accepts a chapter or source update.
2. The agent records author confirmation.
3. `write-session-check` verifies project identity, state fingerprint, and context fingerprint.
4. Proposal readiness verifies evidence and risk.
5. Writer commands acquire `.write.lock`.
6. State changes are written and audited.
7. Snapshots are available for rollback.

## MCP Tool Profiles

The MCP server can expose different tool sets:

- `core`: small read-only writing path
- `writing`: common writing and handoff tools
- `full`: all available tools for advanced local workflows

Read-only mode hides or rejects writer tools.

## Generated Desktop Pack

`desktop-pack` creates a local directory for users and agents:

- HTML entry pages
- JSON status files
- schemas
- command indexes
- evidence templates
- author review pages
- handoff reports
- chapter session files

It is a local guide and status snapshot. It does not prove that a real desktop client has loaded MCP. Real client evidence is recorded separately.
