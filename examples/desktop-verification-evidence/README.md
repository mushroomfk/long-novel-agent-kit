# Desktop Verification Evidence Example

This folder shows a redacted real-client evidence shape for `desktop-checklist`, `desktop-results-doctor`, and `record-desktop-check`.

Files:

- `desktop-codex-read-only-chapter-2-results.json`: fillable results JSON after the user checks a real desktop client.
- `codex-read-only-evidence.txt`: redacted copied evidence text that can be converted into a results JSON by `ingest-desktop-evidence`.
- `desktop-codex-read-only-weak-evidence-results.json`: negative read-only results JSON sample. It intentionally uses generic evidence (`ok`) and must be rejected by `desktop-results-doctor` and saved with `validation.ok=false` by `record-desktop-check`.
- `desktop-cursor-writer-chapter-2-results.json`: fillable writer-mode results JSON showing write-session preflight, proposal readiness, durable writer command, and `author_confirmation` evidence for narrative state writes.
- `desktop-cursor-writer-missing-write-session-preflight-results.json`: negative writer-mode results JSON sample. It intentionally omits `check_write_session` / `write-session-check` proof from `write_session_preflight_gate` and must be rejected by `desktop-results-doctor` and saved with `validation.ok=false` by `record-desktop-check`.
- `desktop-cursor-writer-missing-author-confirmation-results.json`: negative writer-mode results JSON sample. It intentionally omits author-confirmation proof from `acceptance_write_gate` and must be rejected by `desktop-results-doctor` and saved with `validation.ok=false` by `record-desktop-check`.
- `desktop-cursor-writer-confirmation-only-results.json`: negative writer-mode results JSON sample. It includes `author_confirmation` proof but omits durable writer command proof from `acceptance_write_gate`; it must be rejected by `desktop-results-doctor` and saved with `validation.ok=false` by `record-desktop-check`.
- `cursor-writer-author-confirmation-evidence.txt`: redacted copied writer-mode evidence text that can be converted into a results JSON by `ingest-desktop-evidence`.
- `desktop_verifications.jsonl`: saved read-only and writer verification records shaped like `.novel-agent/desktop_verifications.jsonl`.

Replay with a temporary project:

```bash
python cli.py init /tmp/novel-agent-evidence-demo --title "Evidence Demo"
python cli.py desktop-checklist /tmp/novel-agent-evidence-demo --platform codex --mode read-only --chapter 2 --format markdown
python cli.py ingest-desktop-evidence /tmp/novel-agent-evidence-demo --platform codex --mode read-only --chapter 2 --evidence examples/desktop-verification-evidence/codex-read-only-evidence.txt --output desktop-codex-read-only-chapter-2-imported-results.json --format markdown
python cli.py desktop-results-doctor /tmp/novel-agent-evidence-demo --platform codex --mode read-only --chapter 2 --results examples/desktop-verification-evidence/desktop-codex-read-only-chapter-2-results.json --format markdown
python cli.py desktop-results-doctor /tmp/novel-agent-evidence-demo --platform codex --mode read-only --chapter 2 --results examples/desktop-verification-evidence/desktop-codex-read-only-weak-evidence-results.json --format markdown
python cli.py record-desktop-check /tmp/novel-agent-evidence-demo --platform codex --mode read-only --chapter 2 --results examples/desktop-verification-evidence/desktop-codex-read-only-chapter-2-results.json --agent-id codex-local-demo --client-version "Codex Desktop redacted example"
python cli.py desktop-checklist /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --format markdown
python cli.py ingest-desktop-evidence /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --evidence examples/desktop-verification-evidence/cursor-writer-author-confirmation-evidence.txt --output desktop-cursor-writer-chapter-2-imported-results.json --format markdown
python cli.py desktop-results-doctor /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --results examples/desktop-verification-evidence/desktop-cursor-writer-chapter-2-results.json --format markdown
python cli.py desktop-results-doctor /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --results examples/desktop-verification-evidence/desktop-cursor-writer-missing-write-session-preflight-results.json --format markdown
python cli.py desktop-results-doctor /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --results examples/desktop-verification-evidence/desktop-cursor-writer-missing-author-confirmation-results.json --format markdown
python cli.py desktop-results-doctor /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --results examples/desktop-verification-evidence/desktop-cursor-writer-confirmation-only-results.json --format markdown
python cli.py record-desktop-check /tmp/novel-agent-evidence-demo --platform cursor --mode writer --chapter 2 --results examples/desktop-verification-evidence/desktop-cursor-writer-chapter-2-results.json --agent-id cursor-local-demo --client-version "Cursor redacted example"
python cli.py project-status /tmp/novel-agent-evidence-demo --platform codex --format markdown
python cli.py project-status /tmp/novel-agent-evidence-demo --platform cursor --format markdown
```

Evidence rules:

- Keep evidence short.
- Use `ingest-desktop-evidence` to convert copied tool lists, tool-call summaries, filled result JSON, tool JSON snippets, or NDJSON tool transcripts into a result JSON, then review the generated file, match details, warnings, and `desktop-results-doctor` report before recording it.
- In a generated `desktop-pack`, paste copied client evidence into `desktop-evidence.txt`, follow `evidence-to-results.md`, review the generated `imported-...results.json`, run `desktop-results-doctor`, then record only after the imported checks are correct.
- Evidence import and `record-desktop-check` redact common API keys and tokens, but real client evidence should still be short proof snippets rather than full manuscripts, private notes, or unrelated logs.
- Do not include API keys, access tokens, personal information, full private manuscripts, or screenshots with private content.
- Mark a check as `passed` only after confirming it inside the real desktop client.
- `range_readiness_gate` evidence must show `build_chapter_range_readiness` or CLI `chapter-range-readiness`; a `needs_files` result is acceptable when draft files are not present, as long as the proof shows the real desktop client called the tool.
- In writer mode, `write_session_preflight_gate` evidence must show `check_write_session` or CLI `write-session-check` before durable writer commands, `proposal_readiness_gate` evidence must show `build_proposal_readiness` or CLI `proposal-readiness` before apply, and `acceptance_write_gate` evidence must show both durable writer command names (`record_chapter`, `propose_after_write_updates`, or `apply_after_write_updates`) and `author_confirmation` or CLI `--author-confirmation`; otherwise `record-desktop-check` keeps the record but marks `validation.ok=false`.
- Local `desktop-verify` or `doctor --start-mcp-test` output is useful setup evidence, but it does not replace real desktop client evidence.
