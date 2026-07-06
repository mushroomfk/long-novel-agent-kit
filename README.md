# Long Novel Agent Kit

Local continuity tools for long-form novel writing with desktop agents.

Long Novel Agent Kit lets Codex, Claude Desktop, Cursor, and other local desktop agents keep durable novel state outside a chat window. The agent still uses its own model, retrieval, PDF parsing, web reading, image understanding, and long-context ability. This kit provides the local memory and safety protocol that long novels need: chapter-safe context, facts, source summaries, research notes, character state, foreshadowing, plot debts, handoff reports, and post-write update review.

[中文说明](README.zh-CN.md)

## Why This Exists

Long novels fail when continuity lives only in a single chat:

- old facts disappear when the context window rolls over
- a new desktop agent cannot see what the last agent decided
- research and source summaries are used once and then lost
- future reveals leak into early chapters
- accepted chapters do not leave a clean handoff

This kit stores accepted continuity in a local `.novel-agent/` folder inside the novel project. Any capable local desktop agent can read that state through MCP or CLI.

## What It Does

- Builds chapter-safe context before drafting.
- Records source summaries, research notes, conflicts, facts, character state, and plot debts.
- Checks drafts against required and forbidden rules, future markers, structured facts, resolved conflicts, and chapter contracts.
- Requires author confirmation before durable writer operations.
- Creates handoff packets so another local agent can continue the same book.
- Generates local desktop packs for non-technical users.
- Builds no-Python handoff bundles with standalone CLI and MCP executables.

## What It Does Not Do

- It does not run an LLM.
- It does not include embedding search.
- It does not parse PDF/OCR/web pages by itself.
- It does not upload manuscripts.
- It does not require a server for local desktop use.

Those jobs belong to the host desktop agent. This kit persists and verifies the continuity layer.

## Repository Layout

```text
.
├── cli.py                         # local CLI
├── server.py                      # stdio MCP server
├── install.py                     # local skill and MCP installer
├── SKILL.md                       # agent skill instructions
├── schemas/                       # JSON schemas for proposals and desktop packs
├── assets/review-panel.html       # local static review panel
├── examples/                      # smoke, handoff, evidence, and adversarial examples
├── scripts/verify_agent_kit.py    # full regression check
└── scripts/adversarial_release_check.py
```

## Requirements

- Python 3.10 or newer for source-based CLI/MCP usage.
- A local desktop agent that can start stdio MCP or run shell commands.
- Optional: PyInstaller when building no-Python runtime bundles.

The generated no-Python runtime bundle does not require Python on the target computer.

## Quick Start

Create a novel project:

```bash
python cli.py init ./my-novel --title "My Novel"
```

Build chapter context before drafting:

```bash
python cli.py prepare-session ./my-novel --chapter 1 --platform codex --mode read-only --format markdown
```

Check a draft:

```bash
python cli.py check-chapter ./my-novel --chapter 1 --file chapters/001.md --format markdown
```

Start the MCP server in read-only mode:

```bash
python server.py --read-only --tool-profile core
```

Generate a desktop setup guide:

```bash
python cli.py desktop-setup ./my-novel --platform codex --mode read-only --format markdown
```

## No-Python Handoff Bundle

Build standalone runtime files on the same operating system as the target computer:

```bash
python cli.py standalone-build \
  --output-dir release/long-novel-agent-runtime-macos-arm64 \
  --target-os macos \
  --apply \
  --force \
  --format json
```

Create a handoff bundle:

```bash
release/long-novel-agent-runtime-macos-arm64/long-novel-agent desktop-handoff-bundle ./my-novel \
  --platform codex \
  --mode read-only \
  --chapter 1 \
  --runtime-dir release/long-novel-agent-runtime-macos-arm64 \
  --output-dir release/my-novel-agent-bundle \
  --archive \
  --force \
  --format json
```

On the target computer:

1. Unzip the bundle.
2. Run `START_HERE.command`, `START_HERE.sh`, `START_HERE.ps1`, or `START_HERE.cmd`.
3. Use the refreshed MCP config snippets in `mcp-configs/current/`.
4. Give `agent-read-me-first.md` to the desktop agent.

## Verification

Run the full regression suite:

```bash
python scripts/verify_agent_kit.py
```

Run the stronger release gate:

```bash
python scripts/adversarial_release_check.py
```

An optional GitHub Actions workflow template is available at
`docs/github-actions-verify.yml`. Repository maintainers with `workflow` scope
can copy it to `.github/workflows/verify.yml`.

The adversarial release gate checks:

- no generated Python caches or local machine paths
- JSON and JSONL parseability
- Python syntax for core scripts
- bundled schema availability
- full continuity, handoff, proposal, desktop-pack, MCP, and write-guard regression coverage

## Safety Model

The default MCP mode is read-only. Writer mode is intentionally gated:

- author confirmation is required for durable state writes
- write-session checks compare project identity, state hash, and chapter context hash
- high-risk proposal changes require author review
- rollback snapshots are created before applied proposal updates

The durable state folder is `.novel-agent/`. Source manuscripts stay local.

## Supported Local Agent Paths

- CLI-only agents can run `python cli.py ...`.
- MCP-capable agents can start `python server.py --read-only`.
- Codex and Cursor local config paths can be generated automatically by `install.py`.
- Claude Desktop and generic JSON MCP clients can use explicit config snippets.

Remote connector-only platforms are outside this local stdio MCP flow.

## License

MIT. See [LICENSE](LICENSE).
