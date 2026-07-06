# Desktop Agent Workflow

Use this workflow when a local desktop agent is writing, revising, checking, or handing off a long novel.

## Role Split

| Work | Desktop agent | Long Novel Agent Kit |
| --- | --- | --- |
| Source reading | Reads old drafts, files, PDFs, OCR, web pages, notes, images | Stores confirmed summaries and chapter visibility |
| Reasoning and prose | Uses the model and long context to plan, draft, and revise | Provides durable context and checks |
| Project memory | May remember within the current chat | Stores accepted state in `.novel-agent/` |
| Continuity checks | Interprets reports and rewrites text | Detects rule, fact, contract, and future-boundary issues |
| Post-write updates | Proposes what changed | Validates, reviews, applies, audits, and snapshots updates |
| Handoff | Explains what it did | Records activity and builds takeover reports |

## Required Sequence

| Step | Required action | Why it exists |
| --- | --- | --- |
| 1 | Initialize or import the project | Creates durable state instead of relying on scattered files |
| 2 | Add confirmed source summaries and research notes | Makes source material reusable in future sessions |
| 3 | Call `prepare-session` or `build-context` before drafting | Builds chapter-safe context and fingerprints |
| 4 | Draft with the returned context | Keeps prose generation inside visible continuity boundaries |
| 5 | Run `check-chapter` or `chapter-readiness` | Finds violations before the author sees the draft as ready |
| 6 | Use `chapter-delivery` for author handoff | Presents known issues and post-acceptance steps |
| 7 | If accepted, run writer commands with author confirmation | Records accepted text and durable continuity changes |
| 8 | Use `handoff-report` and activity logs before another agent takes over | Lets the next agent continue without chat history |

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
- `build_chapter_revision_prompt`
- `build_chapter_delivery_packet`
- `build_handoff_report`
- `build_agent_activity_report`
- `build_handoff_integrity_report`
- `doctor`

Read-only tools may produce commands, plans, reports, HTML, or JSON. They do not change `.novel-agent/`.

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
python cli.py proposal-template ./my-novel --chapter 3 --file chapters/003.md
python cli.py propose-after-write ./my-novel --chapter 3 --file chapters/003.md --updates updates.json --author-confirmation "Author reviewed chapter 3 updates."
python cli.py proposal-readiness ./my-novel --proposal-id proposal-...
python cli.py apply-after-write ./my-novel --proposal-id proposal-... --author-confirmation "Author approved proposal proposal-..."
```

## Handoff Between Agents

Before a new desktop agent takes over:

```bash
python cli.py handoff-readiness ./my-novel --chapter 4 --incoming-agent-id codex-local --incoming-platform codex --mode read-only --format markdown
python cli.py handoff-report ./my-novel --chapter 4 --format markdown
```

The next agent should treat the handoff report and `.novel-agent/` state as durable continuity. Chat history is not durable memory.

## What The Agent Should Never Do

- Do not draft a target chapter from chat history alone.
- Do not apply proposal updates without author confirmation.
- Do not enable writer MCP mode only because read-only tools are inconvenient.
- Do not treat a generated desktop pack as fresh after the project state changes; run freshness checks.
- Do not treat local MCP config success as proof that the real GUI client can call the tools; record desktop evidence when that matters.
