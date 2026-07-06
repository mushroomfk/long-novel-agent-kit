# Desktop Handoff Relay Example

This example demonstrates a local desktop-agent relay:

1. Agent A initializes `.novel-agent/`, declares capabilities, writes chapter 1, and records the accepted chapter.
2. Agent A submits a structured post-write proposal from `agent-a-updates.json` and applies it after validation.
3. Agent B records real desktop-client verification evidence for writer mode, including write-session preflight, proposal readiness, durable writer command, and author-confirmation proof.
4. Agent B calls `handoff-readiness`, then opens a chapter session for chapter 2.
5. Agent B drafts chapter 2 from `agent-b-draft-002.md`, checks continuity, and records the accepted chapter.

Run the full repository verifier to execute this flow in a temporary directory:

```bash
python agent-kits/long-novel-agent/scripts/verify_agent_kit.py
```

Manual commands, using a copied project directory:

```bash
python cli.py init-wizard /path/to/relay --title "Desktop Handoff Relay" --target-chapters 6 --required "ledger page is missing" --forbidden "copper key leaves Mara" --agent-id agent-a-codex --platform codex --capabilities mcp,cli,file-read,file-write
python cli.py record-chapter /path/to/relay --chapter 1 --file examples/desktop-handoff-relay/agent-a-chapter-001.md --handoff "Agent B should trace the missing ledger page without moving the copper key away from Mara."
python cli.py propose-after-write /path/to/relay --chapter 1 --file examples/desktop-handoff-relay/agent-a-chapter-001.md --updates examples/desktop-handoff-relay/agent-a-updates.json
python cli.py validate-proposal /path/to/relay --proposal-id proposal-id
python cli.py diff-proposal /path/to/relay --proposal-id proposal-id
python cli.py apply-after-write /path/to/relay --proposal-id proposal-id
python cli.py declare-agent /path/to/relay --agent-id agent-b-cursor --platform cursor --capabilities mcp,cli,file-read,file-write
python cli.py handoff-readiness /path/to/relay --chapter 2 --incoming-agent-id agent-b-cursor --incoming-platform cursor --mode writer --format markdown
python cli.py prepare-session /path/to/relay --chapter 2 --platform cursor --mode writer --format markdown
python cli.py check-chapter /path/to/relay --chapter 2 --file examples/desktop-handoff-relay/agent-b-draft-002.md
```

The fixed verifier generates relay checklist evidence dynamically because real desktop-client screenshots or transcripts are local to each user. For the redacted evidence file shape, see `../desktop-verification-evidence/`.
