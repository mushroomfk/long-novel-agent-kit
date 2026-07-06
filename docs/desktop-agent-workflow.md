# Desktop Agent Workflow

Use this workflow when a local desktop agent is writing or revising a long novel.

## Roles

The desktop agent handles:

- model calls
- file parsing
- long-context reading
- PDF/OCR/web/image understanding
- drafting and rewriting prose

Long Novel Agent Kit handles:

- durable continuity state
- chapter-safe context
- facts, sources, research, conflicts, and plot debts
- draft checks
- post-write proposal review
- cross-agent handoff

## Required Sequence

1. Initialize or import the project.
2. Add confirmed source summaries and research notes.
3. Call `prepare-session` or `build-context` before drafting.
4. Draft with the returned chapter context.
5. Run `check-chapter` before presenting the draft as ready.
6. If the author accepts the chapter, record it and propose state updates.
7. Apply proposal updates only after author confirmation.
8. Build a handoff report before another agent takes over.

## Read-Only MCP

Read-only mode is the recommended default:

```bash
python server.py --read-only --tool-profile core
```

Useful read-only tools include:

- `prepare_chapter_session`
- `build_chapter_context`
- `build_chapter_context_brief`
- `check_chapter`
- `diff_chapter_contract`
- `build_chapter_delivery_packet`
- `build_handoff_report`
- `doctor`

## Writer Mode

Writer mode can change `.novel-agent/`. Use it only when the author explicitly allows it.

Before durable writes:

- run a fresh chapter session
- check project identity and state fingerprint
- check chapter context fingerprint
- verify proposal readiness
- include author confirmation

The safe path is:

```bash
python cli.py prepare-session ./my-novel --chapter 3 --platform codex --mode writer --format json
python cli.py write-session-check ./my-novel --chapter 3 --expected-project-id ... --expected-state-sha256 ... --expected-context-hash ...
python cli.py record-chapter ./my-novel --chapter 3 --file chapters/003.md --author-confirmation "Author accepted chapter 3."
```

## Handoff Between Agents

Before a new desktop agent takes over:

```bash
python cli.py handoff-readiness ./my-novel --chapter 4 --incoming-agent-id codex-local --incoming-platform codex --mode read-only --format markdown
python cli.py handoff-report ./my-novel --chapter 4 --format markdown
```

The next agent should treat the handoff report and `.novel-agent/` state as durable continuity. Chat history is not durable memory.
