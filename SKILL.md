---
name: long-novel-agent
description: Long-form novel continuity kit for local desktop agents. Use when planning, initializing, continuing, revising, checking, or handing off long novels with many chapters, source notes, structured facts, character states, foreshadowing, plot debts, chapter contracts, research notes, or existing manuscripts. Provides a local CLI and MCP server for maintaining .novel-agent project memory, desktop bootstrap, onboarding packs, setup guides, client verification records and platform/mode verification matrix, agent activity handoff logs and quality reports, handoff integrity and range reports, project readiness status, handoff readiness checks, local MCP install lifecycle, quickstart setup, workflow templates, initialization wizard, Gaoxia import, state packages, chapter-safe context, continuity checks and audits, chapter revision prompts, handoff reports, proposal validation and review packets, audit logs, read-only MCP, and rollback snapshots.
---

# Long Novel Agent

Use this skill to write long novels with durable continuity across sessions and across different agents. Let the host agent use its own parsing, search, web, PDF/OCR, image, and long-context abilities; use this kit to persist accepted facts and produce chapter-safe context.

## Required Workflow

For a local smoke test after installation, copy `examples/mini-gaoxia-project/` to a writable directory and run `import-gaoxia`, `build-context --chapter 2`, and `check-chapter` against a draft chapter.

For a local desktop-agent relay example, read `examples/desktop-handoff-relay/README.md`. It shows Agent A recording chapter 1 and applying a proposal, then Agent B using `handoff-readiness`, `prepare-session`, and `check-chapter` for chapter 2.

For a multi-chapter continuity example, read `examples/longform-continuity-chain/README.md`. It records chapters 1-6, applies structured post-write updates, checks range readiness for chapters 4-6, builds chapter 7 context, and checks a deliberately invalid chapter 7 draft for prop, relationship, location, life-state, forbidden-phrase, required-phrase, and future-marker violations. The full verifier also generates a temporary 20-chapter stress project, applies post-write updates for every chapter, builds chapter 21 context, checks `context_budget` and read-first `context_brief`, and checks a deliberately invalid chapter 21 draft.

For redacted real-client evidence shape, read `examples/desktop-verification-evidence/README.md`. It shows read-only and writer `desktop-checklist` results JSON, weak-evidence, missing-write-session-preflight, missing-author-confirmation, and confirmation-only negative samples, writer write-session preflight evidence, writer proposal readiness evidence, writer command proof, writer `author_confirmation` evidence, and matching `.novel-agent/desktop_verifications.jsonl` record formats.

For a full regression check of this kit, run:

```bash
python /path/to/long-novel-agent/scripts/verify_agent_kit.py
```

To inspect a local MCP install after adding the config snippet:

```bash
python /path/to/long-novel-agent/cli.py doctor /path/to/novel --install-config ~/.codex/config.toml --start-mcp-test
```

When a project state exists, `--start-mcp-test` starts the configured stdio MCP server, lists tools, and calls `prepare_chapter_session` through MCP. This checks the same read path a desktop agent uses before drafting.

To generate a platform-specific local desktop setup guide before editing config files:

```bash
python /path/to/long-novel-agent/cli.py desktop-setup /path/to/novel --platform codex --mode read-only --format markdown
python /path/to/long-novel-agent/cli.py desktop-setup /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --format markdown
```

Use the setup guide to get the MCP config command, upgrade command, uninstall command, server command, quickstart dry-run, quickstart write command, `doctor`, and `prepare-session` command for the selected desktop agent.

To generate a plain-language onboarding guide that sequences local install, project initialization, local verification, real desktop-client evidence, and the first chapter session:

```bash
python /path/to/long-novel-agent/cli.py desktop-onboard /path/to/novel --platform codex --mode read-only --format markdown
python /path/to/long-novel-agent/cli.py desktop-onboard /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --chapter 12 --write-guide --format markdown
```

Use `desktop-onboard` as the ordinary desktop-agent entry point. The guide itself does not write `.novel-agent/` or MCP config files. CLI `--write-guide` only writes the rendered Markdown guide; installation, quickstart, and desktop-check recording still require the explicit commands shown in the guide.

To plan or execute the local desktop setup from one CLI command:

```bash
python /path/to/long-novel-agent/cli.py desktop-bootstrap /path/to/novel --platform codex --mode read-only --format markdown
python /path/to/long-novel-agent/cli.py desktop-bootstrap /path/to/novel --platform codex --mode read-only --apply --write-guide --format markdown
python /path/to/long-novel-agent/cli.py desktop-bootstrap /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --apply --write-guide --format markdown
```

`desktop-bootstrap` is CLI-only. Without `--apply`, it only prints the planned install, quickstart, verification, and optional guide commands. With `--apply`, it may write the installed skill directory, desktop MCP config, `.novel-agent/`, and the optional Markdown guide. Real desktop-client evidence still requires opening the GUI client and recording `desktop-checklist` results.

To write a local onboarding and evidence pack that a user can hand to a desktop agent:

```bash
python /path/to/long-novel-agent/cli.py desktop-pack /path/to/novel --platform codex --mode read-only --chapter 1 --format markdown
python /path/to/long-novel-agent/cli.py desktop-pack /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --chapter 12 --output-dir desktop-pack --format markdown
python /path/to/long-novel-agent/cli.py desktop-pack /path/to/novel --platform codex --mode read-only --chapter 1 --archive --format markdown
```

`desktop-pack` writes `first-three.md`, `first-three.json`, `first-three.html`, and `first-three.schema.json` as the first read-only entry for ordinary users. Generated `open-local.*` launchers open `first-three.html` first. The JSON names exactly three files to open next: `user-steps.html`, `local-summary.html`, and `agent-startup-prompt.md`. Validate `first-three.json` against `first-three.schema.json` when local schema validation is available. This entry writes no `.novel-agent/`, local MCP config, pack files, or desktop verification record.

`desktop-pack` writes `local-summary.md`, `local-summary.json`, `local-summary.html`, and `local-summary.schema.json` as a short read-only status entry for ordinary users and local desktop agents. The summary combines local install status, pack integrity, pack and chapter-session freshness, project initialization, author-review queue state, and real desktop-client evidence. It also exposes seven read-only quick links for the five-step path, pack health, project initialization, author actions, accepted-chapter write review, real-client evidence, and chapter session, with one recommended link chosen from the current status. Validate `local-summary.json` against `local-summary.schema.json` when local schema validation is available.

To refresh that summary for an already generated desktop pack:

```bash
python /path/to/long-novel-agent/cli.py local-summary /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py local-summary /path/to/pack --format json
```

`local-summary` and MCP `build_desktop_local_summary` are read-only. They read the generated pack directory and project state, then print or return the current summary. They write no `.novel-agent/`, local MCP config, pack files, or desktop verification record.

`desktop-pack` also writes `user-steps.md`, `user-steps.json`, `user-steps.html`, and `user-steps.schema.json` as the ordinary-user five-step route through the current pack state. Open it after `first-three.html` when a normal user wants the full five-step browser route; generated `open-local.*` launchers open `first-three.html` first and keep `user-steps.html` plus `index.html` as follow-up files. Use the JSON when another desktop agent needs to parse the route, and validate it against `user-steps.schema.json` when local schema validation is available. Use it before opening longer reports when a user or desktop agent is unsure whether to repair a copied pack, initialize the project, collect real desktop-client evidence, resolve author-review items, or start the target chapter.

```bash
python /path/to/long-novel-agent/cli.py desktop-user-steps /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py desktop-user-steps /path/to/pack --format json
python /path/to/long-novel-agent/cli.py desktop-user-steps /path/to/pack --format html
```

`desktop-user-steps` and MCP `build_desktop_user_steps` are read-only. They read existing generated pack files and return five ordered steps with files, commands, expected result, stop condition, and write boundary. They write no `.novel-agent/`, local MCP config, pack files, or desktop verification record.

To ask the kit which local desktop-agent action should happen next:

```bash
python /path/to/long-novel-agent/cli.py desktop-next-step /path/to/novel --platform codex --mode read-only --chapter 1 --format markdown
```

`desktop-next-step` and MCP `build_desktop_next_step` are read-only. They inspect project status, local MCP config, selected platform/mode desktop evidence, author-review queue, and target chapter, then return `primary_action`, follow-up actions, commands, and write boundaries. They do not write `.novel-agent/`, change local MCP config, write pack files, or record real desktop-client evidence. Generated desktop packs also write read-only `desktop-next-step.md` and `desktop-next-step.json` snapshots, and include `desktop_next_step` in `commands.json` and `commands.txt` for refreshing the report after project files or local config change.

To diagnose what still needs attention before a local desktop agent writes or takes over:

```bash
python /path/to/long-novel-agent/cli.py desktop-diagnostics /path/to/novel --platform codex --mode read-only --chapter 1 --format markdown
python /path/to/long-novel-agent/cli.py desktop-diagnostics /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --pack-dir /path/to/pack --chapter 12 --format markdown
```

`desktop-diagnostics` and MCP `build_desktop_diagnostics` are read-only. They aggregate local MCP config status, `.novel-agent` initialization, project readiness, author-review queue, selected platform/mode real desktop-client evidence, writer authorization when relevant, and optional pack doctor/readiness results. They return checks, blockers, review items, warnings, recommended commands, and the `desktop-next-step` primary action. They do not write `.novel-agent/`, change local MCP config, write pack files, or record real desktop-client evidence. Generated desktop packs write read-only `desktop-diagnostics.md`, `desktop-diagnostics.json`, and `desktop-diagnostics.schema.json` snapshots, and include the live `desktop_diagnostics` command in `commands.json` and `commands.txt` for refreshing the report with `--pack-dir`.

To recompute handoff readiness for an already generated desktop pack:

```bash
python /path/to/long-novel-agent/cli.py desktop-pack-readiness /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py desktop-pack-readiness /path/to/pack --format json
```

`desktop-pack-readiness` and MCP `build_desktop_pack_readiness` are read-only. They read the current pack directory, `metadata.json`, `commands.json`, project-status, author-review, evidence-review, handoff, chapter-session snapshots, project-state freshness, and chapter-session freshness, then print or return the same readiness shape as `desktop-pack-readiness.json`. They write no `.novel-agent/`, local MCP config, pack files, or desktop verification record, so they do not change `pack-manifest.json`.

To check whether an already generated desktop pack still matches the current tracked `.novel-agent` state:

```bash
python /path/to/long-novel-agent/cli.py pack-freshness /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py pack-freshness /path/to/pack --format json
python /path/to/long-novel-agent/cli.py chapter-session-freshness /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py chapter-session-freshness /path/to/pack --format json
```

`pack-freshness` and MCP `build_desktop_pack_freshness` are read-only. `desktop-pack` records a tracked `.novel-agent` state fingerprint in `metadata.json`; this command compares that baseline with the current project state and returns `fresh`, `stale`, `not_initialized`, `no_baseline`, `missing_metadata`, `missing_project_dir`, or `project_missing`. It writes no `.novel-agent/`, local MCP config, pack files, or desktop verification record.

`chapter-session-freshness` and MCP `build_chapter_session_freshness` are read-only. `prepare-session` and `desktop-pack` include a tracked `.novel-agent` state fingerprint in `chapter-session.json`; this command compares that snapshot with the current project state and returns `fresh`, `stale`, `not_initialized`, `no_baseline`, `missing_chapter_session`, `missing_project_dir`, or `project_missing`. `desktop-pack-readiness` and `pack-doctor` also include this check. It writes no `.novel-agent/`, local MCP config, pack files, or desktop verification record.

Generated `starter-brief`, `agent-startup-prompt`, `agent-takeover`, and `pack-index.json` include `pack_freshness_status` and `chapter_session_freshness_status`, list `metadata.json` in their first-read files, and expose the `pack_freshness` and `chapter_session_freshness` commands. Before drafting from a generated desktop pack, run both freshness checks; if either reports `stale`, regenerate the pack or use live `prepare-session` context.

To validate generated desktop-pack JSON files against the schema files shipped inside the same pack:

```bash
python /path/to/long-novel-agent/cli.py pack-schema-check /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py pack-schema-check /path/to/pack --format json
```

`pack-schema-check` and MCP `build_desktop_pack_schema_check` are read-only. They check the generated pack index, commands index, commands, local summary, project wizard, source intake, desktop next-step, desktop pack readiness, starter brief, user steps, agent takeover, agent startup prompt, troubleshooting, author actions, acceptance review, desktop evidence session, desktop evidence runbook, desktop evidence review, writer-mode approval, chapter session, handoff report, and result JSON template against their local schema files. They also check key cross-references between `pack-index.json`, `commands.json`, `commands-index.json`, and `pack-manifest.json`: referenced files, startup order files, manifest entries, command counts, command groups, stage indexes, and important command refs. They write no `.novel-agent/`, local MCP config, pack files, or desktop verification record. The same check is also included in `pack-doctor`.

Generated desktop packs include `starter-brief.md`, `starter-brief.json`, and `starter-brief.schema.json` as the shortest local-only route for a desktop agent that just received the pack. Validate `starter-brief.json` when possible, then read it before deeper packet files. Refresh it with `starter-brief /path/to/pack --format markdown` or MCP `build_starter_brief` after copied pack files changed. It is read-only and does not write `.novel-agent/`, local MCP config, pack files, or real desktop-client evidence.

Generated desktop packs include `commands-index.md`, `commands-index.json`, and `commands-index.schema.json`. Validate `commands-index.json` when possible, then use it to choose between opening the pack, pack integrity checks, local install, project setup, chapter start, handoff, draft review, desktop evidence, and writer mode, and to review write boundaries before selecting exact commands.

Generated desktop packs also include `agent-takeover.md`, `agent-takeover.json`, and `agent-takeover.schema.json`. Validate `agent-takeover.json` before a local desktop agent starts work from a copied pack. It is a read-only takeover packet that summarizes whether drafting can start, which files to read first, which handoff and continuity checks matter, and which commands require author review or writer mode. Refresh it with `agent-takeover-from-pack /path/to/pack --format markdown` or MCP `build_agent_takeover` when copied pack files, evidence, handoff reports, or chapter-session snapshots changed after pack generation. It writes no `.novel-agent/`, local MCP config, pack files, or real desktop-client evidence.

Generated desktop packs also include `agent-startup-prompt.md`, `agent-startup-prompt.json`, and `agent-startup-prompt.schema.json`. Use this packet when a local desktop agent needs a copy-ready first message for the current pack state. Refresh it with `agent-startup-prompt-from-pack /path/to/pack --format markdown` after copied pack files, evidence, or author-review state changed. MCP `build_agent_startup_prompt` can build the same prompt from a project directory before generating a pack. Both paths are read-only: they do not write `.novel-agent/`, change local MCP config, write pack files, or record real desktop-client evidence.

Generated desktop packs also include `troubleshooting.html`, `troubleshooting.md`, `troubleshooting.json`, and `troubleshooting.schema.json`. Use the HTML page when a normal user needs a local browser view of symptoms and write boundaries. Use the JSON when a local desktop agent needs machine-readable symptom guidance for MCP visibility, GUI tool calls, read-only / writer mode mismatch, pending chapter sessions, source intake, continuity check failures, result JSON recording, pack overwrite failures, write locks, or evidence safety warnings. Run `troubleshooting-from-pack /path/to/pack --format markdown` or call MCP `build_desktop_troubleshooting` when copied pack files, commands, or pack metadata changed after pack generation. These troubleshooting files are read-only and do not write `.novel-agent/`, change local MCP config, write pack files, or record real desktop-client evidence.

Generated desktop packs also include `author-actions.md`, `author-actions.json`, and `author-actions.schema.json`. Use them when the author needs one checklist instead of reading every pack file. The JSON groups pack integrity, author review, draft review, real desktop-client evidence, and writer-mode authorization into action groups with required files, command references, author-confirmation flags, and write boundaries. Run `author-actions /path/to/pack --format markdown` or call MCP `build_author_actions` when copied pack files, evidence, or author-review state changed after pack generation. It is read-only and does not approve, record, or apply any durable state change.

Generated desktop packs also include `acceptance-review.html`, `acceptance-review.md`, `acceptance-review.json`, and `acceptance-review.schema.json`. Open them after the author accepts a single chapter or a chapter range and before any durable writer command runs. The JSON lists single-chapter and range acceptance paths, writer guards, author confirmation requirements, possible `.novel-agent/` files that separate writer commands may change, and read-only boundaries. Run `acceptance-review-from-pack /path/to/pack --format markdown` or call MCP `build_acceptance_review` when copied pack files, author-review state, or chapter-session snapshots changed after pack generation. It is read-only and does not approve, record, apply, change MCP config, write pack files, or record desktop evidence.

Generated desktop packs also include `writer-mode-approval.md`, `writer-mode-approval.json`, and `writer-mode-approval.schema.json`. Validate `writer-mode-approval.json` before changing a local MCP config from read-only to writer mode. The JSON is a read-only authorization checklist; it does not enable writer mode, write `.novel-agent/`, change MCP config, or record real desktop-client evidence.

Generated `commands.json` is grouped. Use `groups` for the same command groups, `by_stage` for stage filtering, `write_scope_counts` for write-risk review, and `by_key` for exact command strings.

`desktop-evidence-session` and MCP `build_desktop_evidence_session` are read-only. They refresh the real desktop-client evidence session from current project state, selected platform/mode, target chapter, evidence scratchpad path, result JSON path, and imported result JSON path. They return required check IDs, copy prompts, workflow steps, local commands, and write boundaries without writing `.novel-agent/`, changing local MCP config, writing pack files, or recording desktop verification evidence. Use them when a generated `desktop-evidence-session.md` / `desktop-evidence-session.json` snapshot may be stale but the user does not need to regenerate the whole pack.

`desktop-evidence-runbook` and MCP `build_desktop_evidence_runbook` are read-only. They turn the current evidence session into an ordered GUI-client runbook with preflight checks, per-check desktop actions, expected proof, copy prompts, scratchpad headings, result JSON doctor commands, and the final reviewed `record-desktop-check` command. Generated desktop packs include `desktop-evidence-runbook.md`, `desktop-evidence-runbook.json`, and `desktop-evidence-runbook.schema.json`; validate the JSON before following GUI-client evidence steps when local schema validation is available. The runbook itself does not write `.novel-agent/`, local MCP config, pack files, imported results JSON, or verification records.


Pass `--archive` to make `desktop-pack` write a shareable local zip next to the output directory. For an already generated pack, run `archive-pack /path/to/pack --force --format markdown`, use `archive-local.sh --yes`, run `archive-local.ps1 --yes` in Windows PowerShell, run `archive-local.cmd --yes` in Windows Command Prompt, or double-click `archive-local.command` on macOS and type `YES`. The generated `commands.json` and `commands.txt` also include `desktop_pack_archive`, which targets the existing pack directory with `archive-pack`. This only writes the zip next to the pack; it does not write `.novel-agent/`, change MCP config, or record real desktop-client evidence.

After generating or moving a desktop pack, run `python verify-pack.py --json` from the pack directory before handing it to another local desktop agent. If `verify-pack.py` is missing or blocked, run:

```bash
python /path/to/long-novel-agent/cli.py verify-pack /path/to/pack --format markdown
```

Before installing, sharing, or handing the pack to another local desktop agent, run the read-only pack doctor:

```bash
python /path/to/long-novel-agent/cli.py pack-doctor /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py pack-doctor /path/to/pack --format json --output /path/to/pack/pack-doctor.json
```

Pack doctor also runs the generated JSON/schema and cross-reference check, checks pack and chapter-session freshness, then inspects absolute `cli.py`, `install.py`, and `server.py` references in `commands.json`. Missing Kit paths or a `cli.py` version mismatch are warnings so copied packs can still be inspected, but install, verify, source-intake, and writer commands should use an installed or regenerated local Kit before execution.

If the pack was copied to a machine where those command paths are stale, run:

```bash
python /path/to/long-novel-agent/cli.py rebind-pack-kit /path/to/pack --format markdown
python /path/to/long-novel-agent/cli.py rebind-pack-kit /path/to/pack --apply --format markdown
```

Dry-run shows the generated files that would change. Apply updates pack files and refreshes `pack-manifest.json` plus `pack-doctor.json`; it does not write `.novel-agent/` or local MCP config.

When changing `desktop-pack` outputs in this repo, update `examples/desktop-pack-snapshot/snapshot.json`; the regression script compares normalized hashes for every generated pack file.

`desktop-pack` also writes `writer-mode-approval.md`, `writer-mode-approval.json`, and `writer-mode-approval.schema.json`. Open the Markdown and validate the JSON before changing local MCP from read-only to writer mode. They state that writer mode writes only `.novel-agent/` state, requires real desktop-client evidence for `write_session_preflight_gate`, `proposal_readiness_gate`, and `acceptance_write_gate`, requires `check_write_session` or CLI `write-session-check` before durable writer commands, requires `build_proposal_readiness` or CLI `proposal-readiness` before apply, requires durable writer command proof plus `author_confirmation` or CLI `--author-confirmation` for durable writes, and gives recovery paths through `uninstall-local.sh`, `uninstall-local.ps1`, or `uninstall-local.cmd`, reinstalling read-only mode, or `restore-snapshot`. Run `writer-mode-readiness` / `build_writer_mode_readiness` before changing the MCP config; it is read-only and checks project status, read-only or existing writer real-client evidence, author confirmation, writer config status, pending proposals, and high-risk proposals. The generated `commands.json` and `commands.txt` include `writer_mode_readiness`, `writer_pack`, `writer_mcp_config_preview`, `writer_install_apply`, and `writer_desktop_checklist` for the reviewed writer-mode path.

When a desktop agent receives a generated pack, read `first-three.json` first and validate it with `first-three.schema.json` if schema validation is available; when a normal user opens the same pack, open `first-three.html` first. Then read `user-steps.json`, `local-summary.json`, validate them with their local schemas if possible, and run `desktop_user_steps`, `local_summary`, `pack_freshness`, `chapter_session_freshness`, and `pack_schema_check`, or read `pack-schema-check.json` if available. Then read `starter-brief.json`, `agent-startup-prompt.json`, `agent-takeover.json`, `troubleshooting.json`, `author-actions.json`, `acceptance-review.json`, `desktop-next-step.json`, `desktop-diagnostics.json`, `project-status.json`, `chapter-session.json`, `handoff-brief.json`, `continuity-audit.json`, `author-review-queue.json`, `commands-index.json`, and `commands.json`. The first-three entry, user steps, local summary, starter brief, startup prompt, takeover packet, and diagnostics snapshot carry freshness, status, or command references so a copied pack can warn the agent before drafting. The freshness checks, schema and cross-reference check, first-three entry, user steps, local summary, starter brief, startup prompt, takeover packet, diagnostics snapshot, troubleshooting packet, author actions, and acceptance review are static and read-only; refresh live status with `desktop_user_steps`, `local_summary`, `pack_freshness`, `chapter_session_freshness`, `pack_schema_check`, `starter_brief`, `agent_startup_prompt`, `agent_takeover`, `desktop_diagnostics`, `desktop_troubleshooting`, `author_actions`, `acceptance_review`, or the referenced read-only commands when project files, MCP config, or desktop evidence changed after pack generation.

`desktop-pack` also writes `author-review-queue.md` and machine-readable `author-review-queue.json`, and `commands.json` / `commands.txt` include `author_review_queue`. Read it before any durable state write, writer-mode change, or multi-chapter acceptance sequence. CLI `author-review-queue` and MCP `build_author_review_queue` are read-only; they combine pending proposals, high-risk proposal readiness, conflict records that need author decisions, writer-mode authorization checks, and optional chapter-range acceptance blockers. `project-status` returns an `author_review_queue` summary, and `prepare-session` includes the target chapter queue so desktop agents see pending author decisions at their normal starting points. They do not record chapters, apply proposals, write agent activity, or modify local MCP config.

`desktop-pack` also writes `client-compatibility.md` and `client-compatibility.json`. Use them when deciding whether the selected local desktop client is actually usable for this project. They separate `local_mcp_config` proof from `real_desktop_client` proof, show the selected platform and mode status from `desktop-matrix`, list required check IDs, and point to `desktop-verify`, `desktop-checklist`, `desktop-matrix`, and `record-desktop-check`. Prefer the JSON file when another desktop agent needs to read the report programmatically.

For fresh projects, fill `project-wizard.json`, then dry-run before applying:

```bash
python /path/to/long-novel-agent/cli.py quickstart-from-wizard /path/to/novel --answers /path/to/novel/long-novel-agent-desktop-pack/project-wizard.json --format markdown
python /path/to/long-novel-agent/cli.py quickstart-from-wizard /path/to/novel --answers /path/to/novel/long-novel-agent-desktop-pack/project-wizard.json --apply --format markdown
```

`quickstart-from-wizard` is CLI-only. Without `--apply`, it only reads the JSON and returns a quickstart dry-run report. With `--apply`, it calls the existing quickstart flow and may initialize or import `.novel-agent/`.

After a desktop agent parses files, PDFs, webpages, images, or old drafts with its own native abilities, fill `source-intake.json`, inspect it with `source-intake-review.html`, validate it through MCP `validate_source_intake` when MCP is available or CLI dry-run when it is not, then apply it only after the report has no errors and `quality.level` is `ready` or `review`:

```bash
python /path/to/long-novel-agent/cli.py source-intake-template /path/to/novel --platform codex --mode read-only --format markdown
python /path/to/long-novel-agent/cli.py source-intake /path/to/novel --intake /path/to/novel/long-novel-agent-desktop-pack/source-intake.json --format markdown
python /path/to/long-novel-agent/cli.py source-intake /path/to/novel --intake /path/to/novel/long-novel-agent-desktop-pack/source-intake.json --apply --author-confirmation "Author approved source intake after dry-run review." --format markdown
```

`source-intake-template` returns a fillable JSON template without writing files. `source-intake` can batch save enabled source summaries, research notes, resolved conflicts, and structured facts. Without `--apply`, the CLI only validates and reports planned writes plus `quality`, including `ready`, `review`, `needs_revision`, `empty`, or `blocked`. Duplicate entries, missing chapter boundaries, missing reliability notes, and fact entries without evidence source lower the score. Apply refuses `empty` and `needs_revision` by default; use `--allow-low-quality` only when the author explicitly accepts that risk. MCP `build_source_intake_template` returns the same template in read-only mode, and MCP `validate_source_intake` performs the same read-only validation without writing `.novel-agent/`. `source-intake-review.html` is local-only browser preview; MCP validation or CLI dry-run remains the authoritative validation.

Writer operations that change durable narrative state require author confirmation. In CLI use `--author-confirmation "..."`; in MCP pass `author_confirmation`. Use it only after the author accepts the source intake, accepted chapter, fact change, style sample, or proposal.

`prepare-session` and `build-context` include machine-readable fingerprints. `prepare-session` returns `project_identity.project_id`, `project_state_fingerprint.sha256`, and `chapter_context_fingerprint.sha256`; `build-context` returns `context_fingerprint.sha256`. Before a reviewed writer command, run read-only `write-session-check` or MCP `check_write_session` with those expected values. It returns `ready`, `needs_expected_values`, `project_mismatch`, `stale_state`, `stale_context`, `not_initialized`, or `missing_chapter`, and generated desktop packs include the same `write_session_check` command. When a writer command is based on a specific session packet, pass `--expected-project-id`, `--expected-state-sha256`, and, for chapter-scoped writes, `--expected-context-hash`. MCP writer tools accept `expected_project_id`, `expected_state_sha256`, and `expected_context_hash`. If the project is wrong, the `.novel-agent` state changed, or the target chapter context changed, the write is rejected before durable state changes.

To verify a local desktop MCP config after installation:

```bash
python /path/to/long-novel-agent/cli.py desktop-verify /path/to/novel --platform codex --mode read-only --install-config ~/.codex/config.toml --start-mcp-test --format markdown
python /path/to/long-novel-agent/cli.py desktop-verify /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --format markdown
```

Use `desktop-verify` to check that the config contains `long-novel-agent`, points to existing command and `server.py` paths, points to an installed kit whose `cli.py` version and `SKILL.md` content match the current source kit, matches the expected read-only or writer mode, matches the expected MCP tool profile, and can optionally start stdio MCP.

To reduce the number of tools exposed to a local desktop client, pass `--tool-profile core` or `--tool-profile writing` to `server.py`, `install.py`, `desktop-setup`, `desktop-bootstrap`, `desktop-pack`, `desktop-verify`, `desktop-next-step`, `desktop-diagnostics`, `desktop-checklist`, `desktop-evidence-session`, or `agent-startup-prompt`. `full` is the default for existing configs. `core` keeps the chapter-writing path small, `writing` adds setup, evidence, and handoff maintenance tools, and `full` exposes every tool allowed by the selected read-only or writer mode. Hidden tools are omitted from `tools/list`; direct calls to hidden tools are rejected by the MCP server. `desktop-next-step` and `desktop-diagnostics` carry the selected profile into their JSON, Markdown, and recommended commands so the next action does not silently drift back to `full`. Generated desktop packs also show the current profile and smaller-profile guidance in README, `START_HERE.md`, `next-step.md`, and `index.html`, so a normal user can see why the desktop client has many or few visible MCP tools before reinstalling.

To explain why a specific local MCP tool is visible or hidden, use the read-only `tool-visibility` CLI command or MCP `explain_tool_visibility`:

```bash
python /path/to/long-novel-agent/cli.py tool-visibility record_chapter --mode read-only --tool-profile core --format markdown
```

The report distinguishes `tool_profile`, `read_only_mode`, and `unknown_tool`, suggests a matching mode or profile when possible, and returns similar tool names for typos. Generated desktop packs include the same entry as `tool_visibility` in `commands.json` and `commands.txt`. This does not write `.novel-agent/`, local MCP config, desktop pack files, or verification records.

To build a manual checklist for confirming the kit inside a real desktop client:

```bash
python /path/to/long-novel-agent/cli.py desktop-checklist /path/to/novel --platform codex --mode read-only --chapter 1 --format markdown
python /path/to/long-novel-agent/cli.py desktop-checklist /path/to/novel --platform cursor --mode writer --install-config ~/.cursor/mcp.json --chapter 12 --format markdown
python /path/to/long-novel-agent/cli.py desktop-checklist /path/to/novel --platform codex --mode read-only --chapter 1 --write-results-template --format markdown
```

Use `desktop-checklist` after `desktop-verify`. It records what still requires real client evidence: server visibility, tool list visibility, `prepare_chapter_session`, `build_chapter_context`, readiness gates, and writer/read-only boundary checks. It also returns an evidence pack with a result JSON template and the matching `record-desktop-check` command. Pass `--write-results-template` in CLI mode to create the fillable results file inside the novel project; this is a CLI convenience and does not change the read-only MCP tool behavior.

To convert copied desktop-client tool lists, tool-call summaries, filled result JSON, tool JSON snippets such as `chapter-session.json`, or NDJSON tool transcripts into a reviewable results JSON:

```bash
python /path/to/long-novel-agent/cli.py sanitize-desktop-evidence /path/to/novel --evidence desktop-evidence.txt --output desktop-evidence.sanitized.txt --format markdown
python /path/to/long-novel-agent/cli.py desktop-evidence-packet /path/to/novel --platform codex --mode read-only --chapter 1 --evidence desktop-evidence.txt --results-reference imported-desktop-results.json --format markdown
python /path/to/long-novel-agent/cli.py desktop-evidence-review /path/to/novel --platform codex --mode read-only --chapter 1 --evidence desktop-evidence.txt --results-reference imported-desktop-results.json --format markdown
python /path/to/long-novel-agent/cli.py desktop-evidence-review /path/to/novel --platform codex --mode read-only --chapter 1 --evidence desktop-evidence.txt --results-reference imported-desktop-results.json --format html
python /path/to/long-novel-agent/cli.py desktop-evidence-gaps /path/to/novel --platform codex --mode read-only --chapter 1 --evidence desktop-evidence.txt --results-reference imported-desktop-results.json --format markdown
python /path/to/long-novel-agent/cli.py desktop-evidence-runbook /path/to/novel --platform codex --mode read-only --chapter 1 --evidence-file desktop-evidence.txt --results-file desktop-results.json --imported-results-file imported-desktop-results.json --format markdown
python /path/to/long-novel-agent/cli.py ingest-desktop-evidence /path/to/novel --platform codex --mode read-only --chapter 1 --evidence desktop-evidence.txt --output desktop-results.json --format markdown
```

`sanitize-desktop-evidence` and MCP `sanitize_desktop_evidence` redact common API keys, bearer tokens, auth tokens, and access tokens, then shorten oversized lines or payloads before import. The MCP tool is read-only and returns sanitized text. The CLI writes only the requested sanitized output file when `--output` is present; it does not write `.novel-agent/`, local MCP config, verification records, or imported results JSON.

`desktop-evidence-packet` and MCP `build_desktop_evidence_packet` are read-only status tools. They accept pasted evidence text or a results JSON payload, return matched checks, missing required checks, safety warnings, result-doctor status, and next commands, and do not write `.novel-agent/`, local MCP config, pack files, or imported results JSON.

`desktop-evidence-review` and MCP `build_desktop_evidence_review` are read-only review tools. They combine the current evidence session, pasted evidence or results JSON preflight, and saved `.novel-agent/desktop_verifications.jsonl` records into one status report. Use it before `record-desktop-check` when an agent needs to know whether evidence is ready to record, needs revision, is missing, or has already been recorded. It supports Markdown, JSON, and HTML output. Generated desktop packs include `desktop-evidence-review.html`, `desktop-evidence-review.md`, `desktop-evidence-review.json`, and `desktop-evidence-review.schema.json`; the HTML file is for ordinary users to open locally and can load local results JSON, evidence text, or refreshed review JSON for browser-side recheck. When the evidence text uses structured check sections, the HTML page can generate a local results JSON preview in the browser and offers copy/download buttons for that preview. It is only a preview and must still be reviewed before `record-desktop-check`. The JSON file is for desktop agents and schema checks. It does not write `.novel-agent/`, local MCP config, pack files, imported results JSON, or verification records; the HTML download button only saves a user-initiated local browser download.

`desktop-evidence-gaps` and MCP `build_desktop_evidence_gaps` are read-only per-check gap tools. They list each required real desktop-client evidence item as passed, missing, failed, weak, too generic, or needing review, with the next action for that check. Generated desktop packs include `desktop-evidence-gaps.md` and `desktop-evidence-gaps.json`, plus the `desktop_evidence_gaps` command in `commands.json`. It does not write `.novel-agent/`, local MCP config, pack files, imported results JSON, or verification records.

`desktop-evidence-runbook` and MCP `build_desktop_evidence_runbook` are read-only workflow tools. Use the runbook inside the real GUI desktop client before filling or recording results: it lists the exact desktop-side action, expected evidence, proof text to capture, and the local command to run after evidence is collected. Generated desktop packs include the static runbook files, `desktop-evidence-runbook.schema.json`, and a `desktop_evidence_runbook` command in `commands.json`.

`ingest-desktop-evidence` and `ingest_desktop_client_evidence` are read-only analysis tools. They do not write `.novel-agent/`; review the generated `results_payload`, `match_details`, and `warnings` before using `record-desktop-check`. They still accept plain text, filled results JSON, tool JSON snippets, and NDJSON tool events; when the structured scratchpad marker is present, matching uses only the filled `Evidence:` sections. Evidence import and recording redact common API keys and tokens, but users should still keep evidence short and avoid full manuscripts or unrelated private data.

Before recording filled or imported desktop results, run the read-only results doctor:

```bash
python /path/to/long-novel-agent/cli.py desktop-results-doctor /path/to/novel --platform codex --mode read-only --chapter 1 --results desktop-results.json --format markdown
python /path/to/long-novel-agent/cli.py desktop-results-doctor /path/to/novel --platform cursor --mode writer --chapter 12 --results imported-desktop-results.json --format json
```

`desktop-results-doctor` and MCP `build_desktop_results_doctor` reuse the same validation rules as `record-desktop-check` without writing `.novel-agent/`. Empty templates, missing required evidence, failed required checks, unknown check IDs, sensitive evidence warnings, or missing writer write-session preflight / proposal-readiness / author-confirmation proof return `needs_revision`; the CLI exits non-zero on blocking issues. Only use `record-desktop-check` after the doctor returns `ready_to_record`.

After checking inside the real desktop client, record the result only when evidence exists for each required item:

```bash
python /path/to/long-novel-agent/cli.py record-desktop-check /path/to/novel --platform codex --mode read-only --chapter 1 --results desktop-results.json --agent-id codex-local --client-version "Codex Desktop"
python /path/to/long-novel-agent/cli.py list-desktop-checks /path/to/novel --platform codex
python /path/to/long-novel-agent/cli.py desktop-matrix /path/to/novel --format markdown
```

`desktop-results-doctor`, `record-desktop-check`, and the generated `index.html` reject weak required evidence such as `ok`, `done`, `passed`, `已完成`, or `通过`. Evidence must include a real desktop-client tool name, visible server name, returned status, or rejected-call detail.

`record-desktop-check` writes `.novel-agent/desktop_verifications.jsonl`. Required checklist items must be `passed` and include evidence from the real client; missing or weak evidence keeps `validation.ok=false`. `range_readiness_gate` evidence must mention `build_chapter_range_readiness` or CLI `chapter-range-readiness`; a `needs_files` result is acceptable when draft files are not present, as long as the proof shows the real desktop client called the tool. In writer mode, `write_session_preflight_gate` evidence must mention `check_write_session` or CLI `write-session-check`, `proposal_readiness_gate` evidence must mention `build_proposal_readiness` or CLI `proposal-readiness`, and `acceptance_write_gate` evidence must mention a durable writer command (`record_chapter`, `propose_after_write_updates`, or `apply_after_write_updates`) plus `author_confirmation` or CLI `--author-confirmation`; otherwise the saved record remains `validation.ok=false`.
Use `desktop-matrix` or MCP `get_desktop_verification_matrix` to see which local desktop platforms and modes have saved real-client evidence. The matrix only summarizes saved records; it does not prove a client works until `record-desktop-check` has captured evidence from that real GUI client.

Generated desktop packs include both `desktop-matrix.md` and `desktop-matrix.json`. Use the Markdown file for human review and the JSON file when a desktop agent needs to read `filters`, `totals`, `matrix`, `warnings`, recommended actions, required-check coverage, missing IDs, failed IDs, weak-evidence IDs, or evidence-specific gap IDs without parsing prose.

When a desktop agent starts, finishes, or hands off a meaningful writing step, record the activity:

```bash
python /path/to/long-novel-agent/cli.py record-agent-activity /path/to/novel --agent-id codex-local --platform codex --action prepare-session --chapter 12 --summary "Prepared chapter 12 context before drafting." --context-source prepare_chapter_session --context-file chapter-12-context.md --file chapters/012.md
python /path/to/long-novel-agent/cli.py list-agent-activity /path/to/novel --chapter 12 --format markdown
```

`record-agent-activity` writes `.novel-agent/agent_activity.jsonl`. Store concise summaries, changed files, status, and either a context hash or a context file path; the tool records a SHA-256 hash for context files and does not duplicate the full context packet. `handoff-readiness`, `handoff-report`, and `prepare-session` include `recent_agent_activity` for the target chapter, and hide activity from later chapters. `handoff-report` also includes `next_agent_brief` with an incoming-agent copy prompt, must-read state files, startup sequence, write boundaries, and first commands. When `handoff-readiness` receives `--outgoing-agent-id`, it also checks whether that agent has target-visible activity. `list-agent-activity` is visible in read-only MCP mode.

`agent-activity-report` / MCP `build_agent_activity_report` is read-only. Use it before handoff when another desktop agent depends on previous work. It reads `.novel-agent/agent_activity.jsonl`, hides future-chapter activity, checks required actions, flags generic summaries, missing context source/hash/file, and missing related files, then returns `ready`, `ready_with_warnings`, `needs_review`, or `blocked`. It also returns `handoff_quality.score`, a rating, and deductions so the next agent can see whether activity records are strong enough for takeover.

`handoff-integrity` / MCP `build_handoff_integrity_report` is read-only. Use it after `handoff-readiness` and `agent-activity-report` when a desktop agent takes over. It builds the current target-chapter context fingerprint, compares it with the handoff report, activity context hashes, referenced files, pending proposals, high-risk proposals, and optional `expected_context_hash`, then returns `ready`, `ready_with_warnings`, `needs_review`, or `blocked`.

`handoff-range-report` / MCP `build_handoff_range_report` is read-only. Use it before multi-chapter handoff or author presentation. It combines `chapter-range-readiness`, `handoff-integrity`, range-scoped pending proposals, high-risk proposal checks, and recommended next commands, then returns `ready`, `ready_with_warnings`, `needs_review`, or `blocked`.

`chapter-range-delivery` / MCP `build_chapter_range_delivery_packet` is read-only. Use it before presenting several draft chapters to the author. It combines `chapter-range-readiness`, per-chapter `chapter-delivery` packets, `handoff-range-report`, delivery gates, skipped write operations, and post-acceptance commands, then returns `ready_for_author_review`, `ready_with_notes`, `needs_author_review`, `needs_revision`, or `blocked`.

`chapter-range-acceptance-plan` / MCP `build_chapter_range_acceptance_plan` is read-only. Use it after the author accepts several draft chapters and before any durable writer operation. It checks author-confirmation presence, reuses range delivery, returns `pre_write_review`, lists possible `.novel-agent/` write scopes, creates per-chapter `prepare_session_for_write_guard` and `write_session_check` command keys, keeps writer steps unavailable while blockers or author-review items remain, orders `record-chapter`, `proposal-template`, `propose-after-write`, `proposal-readiness`, `apply-after-write`, activity logging, and next-session preparation, and returns `needs_author_confirmation`, `needs_author_review`, `needs_revision`, `blocked`, `ready_with_warnings`, or `ready_to_record`.

`author-review-queue` / MCP `build_author_review_queue` is read-only. Use it before durable state changes, after author acceptance, and before enabling writer MCP mode. It presents pending decisions across proposals, high-risk readiness, conflicts, writer-mode authorization, and optional chapter-range acceptance checks, returning `blocked`, `needs_author_review`, `ready_with_warnings`, or `clear`. `project-status` carries a compact queue summary and `prepare-session` carries the target chapter queue in JSON and Markdown.

To start work on a target chapter, ask for a chapter session packet first. It combines health checks, handoff, open threads, required tool gates, and chapter-safe context in one read-only call:

```bash
python /path/to/long-novel-agent/cli.py prepare-session /path/to/novel --chapter 12 --platform codex --mode writer --format markdown
```

To print host-agent workflow instructions:

```bash
python /path/to/long-novel-agent/cli.py agent-template --platform codex --mode writer --format markdown
python /path/to/long-novel-agent/cli.py agent-template --platform codex --mode read-only --format markdown
```

To generate platform MCP config snippets. Config snippets start MCP in read-only mode by default; use `--writer-mcp` only after the author trusts the host agent to write `.novel-agent/`. When `--mcp-config` points to an existing local config, `install.py` preserves other MCP servers and only adds `long-novel-agent`; use `--upgrade` or `--force` only to replace an existing `long-novel-agent` entry. `--uninstall` removes the installed skill directory and only the `long-novel-agent` MCP entry, preserving other local MCP servers. For Codex and Cursor, `--mcp-config auto` resolves the known local desktop config path. For Claude Desktop and generic JSON clients, pass an explicit config path after checking that app's local MCP settings.

```bash
python /path/to/long-novel-agent/install.py --mcp-platform codex --print-mcp-config
python /path/to/long-novel-agent/install.py --mcp-platform cursor --print-mcp-config
python /path/to/long-novel-agent/install.py --mcp-platform claude-desktop --print-mcp-config
python /path/to/long-novel-agent/install.py --mcp-platform codex --writer-mcp --print-mcp-config
python /path/to/long-novel-agent/install.py --mcp-platform codex --mcp-config auto
python /path/to/long-novel-agent/install.py --mcp-platform cursor --mcp-config auto
python /path/to/long-novel-agent/install.py --mcp-platform codex --mcp-config auto --upgrade
python /path/to/long-novel-agent/install.py --mcp-platform codex --mcp-config auto --uninstall
```

After a config merge, use the printed `quickstart_command` to initialize or import a novel project, then use the printed `doctor_command` to test the installed stdio MCP server and its chapter startup read path.

This local kit targets desktop agents that can start a stdio MCP server or run CLI commands. Remote connector-only platforms are outside this local install flow.

For ordinary users on another computer without Python, build a same-OS standalone runtime first, then create a local handoff bundle:

```bash
python /path/to/long-novel-agent/cli.py standalone-build --output-dir /path/to/runtime --target-os macos --apply --force --format markdown
python /path/to/long-novel-agent/cli.py desktop-handoff-bundle /path/to/novel --platform codex --mode read-only --chapter 1 --runtime-dir /path/to/runtime --archive --format markdown
```

`standalone-build` produces two executables: `long-novel-agent` for CLI commands and `long-novel-agent-mcp` for stdio MCP. `desktop-handoff-bundle` writes a copyable folder with `project/`, `pack/`, `runtime/`, `START_HERE.*`, `agent-read-me-first.md`, no-Python runtime commands, MCP config snippets, a manifest, and an optional zip archive. The target computer does not need Python when both runtime executables are present. If the runtime is missing, the bundle is marked `needs_runtime` and writes `runtime/MISSING_RUNTIME.md`. Writer mode still requires author review and the normal write-session checks before changing `.novel-agent/`.

1. For first-time setup, prefer `quickstart`. It auto-detects a Gaoxia project when `project.json` exists; otherwise it initializes a new `.novel-agent/`, records host-agent capabilities, prepares the first chapter session, runs `doctor`, and writes one audit row. Use `--dry-run` before writing when the project path or rules are uncertain.

```bash
python /path/to/long-novel-agent/cli.py quickstart /path/to/novel --source auto --platform codex --mode writer --dry-run --format markdown
python /path/to/long-novel-agent/cli.py quickstart /path/to/novel --source auto --platform codex --mode writer --summary-only
python /path/to/long-novel-agent/cli.py quickstart /path/to/novel --source auto --platform codex --mode writer --install-config ~/.codex/config.toml --start-mcp-test --format markdown
```

2. Inspect readiness before writing or switching agents:

```bash
python /path/to/long-novel-agent/cli.py project-status /path/to/novel --platform codex --format markdown
```

Use `project-status` to see `ready`, `ready_with_warnings`, `needs_review`, `blocked`, or `not_initialized`, plus blockers, warnings, latest desktop verification, desktop verification matrix, author-review queue summary, counts, and recommended commands.
It also separates local MCP config checks from saved real desktop client evidence in `verification_status`; local config checks are not proof that the GUI desktop client can call the kit. When real desktop evidence fails, `verification_status.real_desktop_client.issue_summary` and Markdown output show the failing checklist items and user-facing messages.
It also shows the latest agent activity record and recommends `list-agent-activity` when a project has been initialized.

For a deeper read-only continuity audit before writing or handing off:

```bash
python /path/to/long-novel-agent/cli.py continuity-audit /path/to/novel --chapter 12 --platform codex --format markdown
```

Use `continuity-audit` to inspect chapter gaps or duplicates, pending and high-risk proposals, proposal validation issues, facts without evidence, facts without chapter or reveal boundaries, source and research boundary gaps, latest handoff text, agent declarations, style samples, and saved real desktop-client evidence. It reports blockers, review items, warnings, recommended commands, and an `action_plan` grouped into `must_fix`, `review`, and `improve` actions without writing `.novel-agent/`.

3. Before assigning the next writing step to another desktop agent, verify handoff readiness:

```bash
python /path/to/long-novel-agent/cli.py handoff-readiness /path/to/novel --chapter 12 --incoming-agent-id codex-local --incoming-platform codex --mode writer --format markdown
```

Use `handoff-readiness` to check project readiness, pending proposals, handoff text, recent agent activity, target chapter context, incoming agent capabilities, outgoing agent activity when provided, and real desktop client evidence. Its Markdown output repeats the real desktop client issue summary so the next agent can see why evidence failed.

4. Initialize the novel workspace manually when quickstart is not appropriate:

```bash
python /path/to/long-novel-agent/cli.py init-wizard /path/to/novel --title "Novel Title" --required "must-use phrase" --forbidden "forbidden phrase" --agent-id codex-local --platform codex --capabilities mcp,cli,file-read,file-write --dry-run
python /path/to/long-novel-agent/cli.py init-wizard /path/to/novel --title "Novel Title" --required "must-use phrase" --forbidden "forbidden phrase" --agent-id codex-local --platform codex --capabilities mcp,cli,file-read,file-write
python /path/to/long-novel-agent/cli.py init /path/to/novel
python /path/to/long-novel-agent/cli.py declare-agent /path/to/novel --agent-id codex-local --platform codex --capabilities mcp,cli,file-read,file-write
```

5. If `/path/to/novel` is an existing Gaoxia project with `project.json`, `chapters/`, `Vault/`, or `.gaoxia/learning/narrative_state.json`, import its continuity state first. This reads Gaoxia files and writes only `.novel-agent/`. After import, run `import-audit` to compare Gaoxia source files with the imported state before another agent relies on it. The audit also detects imported chapter files, architecture files, Vault notes, project memory entries, and narrative-state payloads that changed or disappeared after import.

```bash
python /path/to/long-novel-agent/cli.py import-gaoxia /path/to/novel
python /path/to/long-novel-agent/cli.py import-gaoxia /path/to/novel --dry-run
python /path/to/long-novel-agent/cli.py import-audit /path/to/novel --chapter N --format markdown
```

6. When the agent reads source material, old drafts, PDFs, webpages, or research, save only confirmed summaries:

```bash
python /path/to/long-novel-agent/cli.py add-source /path/to/novel --summary notes.md --source-path notes.md --chapter-range 1-20 --author-confirmation "Author approved this source summary."
python /path/to/long-novel-agent/cli.py add-research /path/to/novel --url https://example.com --summary research.md --chapter-range 8+ --author-confirmation "Author approved this research note."
python /path/to/long-novel-agent/cli.py add-fact /path/to/novel --kind prop --subject 铜钥匙 --predicate holder --object 林追 --forbidden 铜钥匙交给白石商会 --author-confirmation "Author approved this continuity fact."
```

7. Before writing or revising chapter N, call `prepare-session` or `build-context`. Do not rely only on chat history. Use `prepare-session` when a desktop agent is opening the project or switching chapters; it includes `project_identity`, `project_state_fingerprint`, `chapter_context_fingerprint`, `recent_agent_activity`, the target chapter author-review queue, handoff, health checks, chapter context, `context_budget`, and read-first `context_brief`. Use `build-context` when only the raw chapter context is needed. Use `context-brief` or MCP `build_chapter_context_brief` when the host context window is small and the agent needs only the read-first packet. Read `context_brief` first, then inspect the full context. In `context_brief`, prefer rows with low `chapter_distance`; chapter-bounded rows also preserve `chapter_ranges`. If `context_budget.status` is `warning` or `critical`, prioritize the current chapter contract, previous chapter tail, high-risk facts, and open debts before drafting.

```bash
python /path/to/long-novel-agent/cli.py prepare-session /path/to/novel --chapter N --platform codex --mode writer --format markdown
python /path/to/long-novel-agent/cli.py context-brief /path/to/novel --chapter N --format markdown
python /path/to/long-novel-agent/cli.py build-context /path/to/novel --chapter N --format markdown
```

8. Write or revise using the returned chapter context. Treat these priority levels as binding:

- Current user instruction
- Accepted chapter text
- Explicit project rules and conflicts resolved by the author
- Chapter contract
- Visible source and research summaries
- Agent inference

9. Before presenting a single draft to the author, call `chapter-delivery`. It is read-only and bundles draft metadata, `chapter-readiness`, a copyable `chapter-revision-prompt`, handoff report, pending proposals for that chapter, write operations not performed, and post-acceptance commands. If `chapter-delivery` is unavailable or the draft needs repair, call `chapter-revision-prompt` to turn `revision_plan` and `fact_context` into a focused desktop-agent revision prompt. After revising, call `chapter-revision-compare` when the previous draft file is available so the agent can report resolved, new, and remaining issues. If that is unavailable, call `chapter-readiness` and fix blocking issues. Before handing off or presenting several recent drafts, call `chapter-range-readiness`. If the host agent cannot call the combined reports, call `check-chapter` and `diff-contract`.

```bash
python /path/to/long-novel-agent/cli.py chapter-delivery /path/to/novel --chapter N --file chapters/N.md --format markdown
python /path/to/long-novel-agent/cli.py chapter-revision-prompt /path/to/novel --chapter N --file chapters/N.md --format markdown
python /path/to/long-novel-agent/cli.py chapter-revision-compare /path/to/novel --chapter N --before chapters/N.before.md --after chapters/N.md --format markdown
python /path/to/long-novel-agent/cli.py chapter-readiness /path/to/novel --chapter N --file chapters/N.md --format markdown
python /path/to/long-novel-agent/cli.py chapter-range-readiness /path/to/novel --start 10 --end 12 --draft-dir chapters --format markdown
python /path/to/long-novel-agent/cli.py check-chapter /path/to/novel --chapter N --file chapters/N.md
python /path/to/long-novel-agent/cli.py diff-contract /path/to/novel --chapter N --file chapters/N.md
```

10. After the author accepts a chapter, call `chapter-acceptance-plan` first. It is read-only and confirms the delivery status, author-confirmation gate, skipped writes, ordered writer commands, `pre_write_review`, possible `.novel-agent/` write scopes, and the source fields for writer guard values. Run the returned `prepare_session_for_write_guard` command, then run `write-session-check` with the latest `project_id`, state hash, and context hash before executing any writer command. Record the accepted chapter and submit structured post-write updates only when that check returns `ready`. Use `propose-after-write` before durable writes, run `proposal-readiness`, and use `apply-after-write` only after readiness allows it and the author confirms the apply.

```bash
python /path/to/long-novel-agent/cli.py chapter-acceptance-plan /path/to/novel --chapter N --file chapters/N.md --author-confirmation "Author accepted this chapter." --format markdown
python /path/to/long-novel-agent/cli.py prepare-session /path/to/novel --chapter N --mode writer --format json
python /path/to/long-novel-agent/cli.py write-session-check /path/to/novel --chapter N --expected-project-id "<project_id>" --expected-state-sha256 "<state_sha256>" --expected-context-hash "<context_sha256>" --format markdown
python /path/to/long-novel-agent/cli.py record-chapter /path/to/novel --chapter N --file chapters/N.md --author-confirmation "Author accepted this chapter." --expected-project-id "<project_id>" --expected-state-sha256 "<state_sha256>" --expected-context-hash "<context_sha256>"
python /path/to/long-novel-agent/cli.py proposal-template /path/to/novel --chapter N --file chapters/N.md
python /path/to/long-novel-agent/cli.py proposal-schema
python /path/to/long-novel-agent/cli.py propose-after-write /path/to/novel --chapter N --file chapters/N.md --updates updates.json --author-confirmation "Author approved this post-write proposal." --expected-project-id "<project_id>" --expected-state-sha256 "<state_sha256>" --expected-context-hash "<context_sha256>"
python /path/to/long-novel-agent/cli.py validate-proposal /path/to/novel --proposal-id proposal-...
python /path/to/long-novel-agent/cli.py diff-proposal /path/to/novel --proposal-id proposal-...
python /path/to/long-novel-agent/cli.py proposal-review /path/to/novel --proposal-id proposal-... --format markdown
python /path/to/long-novel-agent/cli.py proposal-readiness /path/to/novel --proposal-id proposal-... --format markdown
python /path/to/long-novel-agent/cli.py proposal-review /path/to/novel --proposal-id proposal-... --output-dir proposal-review --format markdown
python /path/to/long-novel-agent/cli.py apply-after-write /path/to/novel --proposal-id proposal-... --author-confirmation "Author approved applying this proposal."
```

`apply-after-write` creates a rollback snapshot before changing durable state. To manually manage snapshots:

```bash
python /path/to/long-novel-agent/cli.py create-snapshot /path/to/novel --reason before-large-rewrite
python /path/to/long-novel-agent/cli.py list-snapshots /path/to/novel
python /path/to/long-novel-agent/cli.py restore-snapshot /path/to/novel --snapshot-id snapshot-id
python /path/to/long-novel-agent/cli.py list-audit /path/to/novel
python /path/to/long-novel-agent/cli.py export-state /path/to/novel --output handoff.novel-agent.zip
python /path/to/long-novel-agent/cli.py import-state /path/to/other-novel --package /path/to/novel/handoff.novel-agent.zip
python /path/to/long-novel-agent/cli.py record-style /path/to/novel --chapter N --file chapters/N.md --author-confirmation "Author approved this style sample."
python /path/to/long-novel-agent/cli.py handoff-readiness /path/to/novel --chapter 13 --incoming-agent-id codex-local --incoming-platform codex --mode writer --format markdown
python /path/to/long-novel-agent/cli.py handoff-report /path/to/novel --chapter 13 --format markdown
```

For manual proposal review, use `proposal-review` or MCP `build_proposal_review` to combine the saved proposal, validation result, durable-state diff, risk markers, next steps, and validate/diff/apply/reject commands. Use `proposal-readiness` or MCP `build_proposal_readiness` as the read-only gate before applying: it returns `ready`, `needs_author_review`, `needs_revision`, `rejected`, or `already_applied`, plus grouped actions and commands. CLI `proposal-review --output-dir` writes `proposal-review.html`, `proposal-review.json`, `proposal.json`, `validation.json`, `diff.json`, `commands.txt`, and `README.md` without changing `.novel-agent/`. You can also open `assets/review-panel.html` in a browser and load proposal, diff, audit, or snapshot JSON. These review pages do not call CLI or apply state changes.

## MCP Tools

When MCP is available, prefer tool calls over shell commands. Start the local server with:

```bash
python /path/to/long-novel-agent/server.py
python /path/to/long-novel-agent/server.py --read-only
```

Available tools:

- `init_project`: create `.novel-agent/`.
- `init_wizard`: plan or initialize a project with rules, source summary, and host-agent metadata. Use `dry_run` before writing.
- `quickstart_project`: initialize or import a local project, declare the host agent, prepare a chapter startup packet, run `doctor`, and return recommended next commands. Use `dry_run` before writing.
- `import_gaoxia_project`: import accepted chapters, architecture files, project Vault notes, and narrative state from an existing Gaoxia project into `.novel-agent/`.
- `audit_gaoxia_import`: return a read-only audit comparing Gaoxia source files with imported `.novel-agent/` chapters, Vault notes, project memory, narrative-state payloads, facts, and target-chapter context. It flags changed or missing imported source files and old import rows that lack source hashes. This is visible in read-only MCP mode.
- `export_state_package`, `import_state_package`: move `.novel-agent/` state between projects or machines as a zip package.
- `get_agent_template`: return host desktop agent workflow instructions for `generic`, `codex`, `claude-desktop`, or `cursor`, in writer or read-only mode.
- `build_desktop_setup_guide`: return a local desktop setup guide with MCP config, server startup, quickstart, `doctor`, and `prepare-session` commands. This is visible in read-only MCP mode.
- `build_desktop_onboarding_guide`: return a plain-language onboarding guide that sequences local MCP install, quickstart, local verification, real desktop-client evidence, and the first chapter session. This is visible in read-only MCP mode.
- `verify_desktop_setup`: verify a local desktop MCP config for this kit, expected mode, paths, and optional stdio MCP startup. This is visible in read-only MCP mode.
- `explain_tool_visibility`: explain whether a local MCP tool is visible for the selected read-only or writer mode and `core / writing / full` tool profile, including blockers, suggested mode or profile, and similar tool names. This is read-only and visible in read-only MCP mode.
- `build_desktop_diagnostics`: return a read-only diagnostic report that combines local MCP config, project state, author-review queue, real desktop-client evidence, writer authorization, and optional pack health. This is visible in read-only MCP mode and in the `core` tool profile.
- `build_desktop_client_checklist`: return a manual checklist for proving the kit works inside a real desktop client. This is visible in read-only MCP mode.
- `build_desktop_local_summary`: refresh the short read-only local desktop summary from a generated desktop pack directory. This is visible in read-only MCP mode.
- `build_desktop_user_steps`: refresh the five-step ordinary-user route from a generated desktop pack directory; it can return JSON, Markdown, or HTML text. This is visible in read-only MCP mode.
- `build_desktop_pack_readiness`: refresh the read-only desktop-pack readiness report from a generated desktop pack directory. This is visible in read-only MCP mode.
- `build_desktop_pack_freshness`: compare a generated desktop pack's recorded project-state fingerprint with the current tracked `.novel-agent` state. This is visible in read-only MCP mode.
- `build_chapter_session_freshness`: compare a generated `chapter-session.json` snapshot with the current tracked `.novel-agent` state before a local desktop agent drafts from a static pack. This is visible in read-only MCP mode.
- `check_write_session`: compare expected project ID, project-state fingerprint, and optional target-chapter context fingerprint with current `.novel-agent` state before a writer command. It is read-only and visible in read-only MCP mode.
- `build_desktop_pack_schema_check`: validate generated desktop-pack JSON files and key cross-references against the schema files shipped inside the same pack. This is visible in read-only MCP mode.
- `build_author_actions`: refresh the read-only author action checklist from a generated desktop pack directory. This is visible in read-only MCP mode.
- `build_agent_takeover`: refresh the read-only desktop-agent takeover packet from a generated desktop pack directory. This is visible in read-only MCP mode.
- `build_desktop_troubleshooting`: refresh the read-only troubleshooting packet from a generated desktop pack directory. This is visible in read-only MCP mode.
- `build_source_intake_template`: return a fillable `source-intake.json` template without writing files or `.novel-agent/`. This is visible in read-only MCP mode.
- `validate_source_intake`: validate a filled `source-intake.json` and return a planned write report plus `quality` level without writing `.novel-agent/`. This is visible in read-only MCP mode.
- `ingest_desktop_client_evidence`: convert pasted real desktop client evidence text, filled result JSON, tool JSON snippets, or NDJSON tool transcripts into checklist results JSON without recording it. This is visible in read-only MCP mode.
- `sanitize_desktop_evidence`: redact secret-like values and shorten oversized copied desktop-client evidence before import. It returns sanitized text and is visible in read-only MCP mode.
- `build_desktop_evidence_packet`: summarize pasted evidence or results JSON into a read-only status packet with matched checks, missing checks, safety warnings, result readiness, and next commands. This is visible in read-only MCP mode.
- `build_desktop_evidence_review`: combine evidence session, pasted evidence or results preflight, and saved desktop verification records into a read-only review status. This is visible in read-only MCP mode.
- `build_desktop_evidence_gaps`: list every required real desktop-client evidence item with missing, failed, weak, too-generic, review-needed, or passed status. This is visible in read-only MCP mode.
- `build_desktop_results_doctor`: validate filled or imported real desktop client results before recording them. It is read-only, returns `ready_to_record` or `needs_revision`, and is visible in read-only MCP mode.
- `build_writer_mode_readiness`: check whether a local desktop agent may switch MCP from read-only to writer mode. It is read-only, checks real desktop-client proof, author confirmation, pending and high-risk proposals, and writer config status, and is visible in read-only MCP mode.
- `build_author_review_queue`: build a read-only queue of decisions the author should review before durable state changes. It combines pending proposals, high-risk proposal readiness, conflict records, writer-mode authorization, and optional chapter-range acceptance blockers. This is visible in read-only MCP mode.
- `record_desktop_client_check`: record real desktop client verification results and evidence in `.novel-agent/desktop_verifications.jsonl`. This is a writer tool.
- `list_desktop_client_checks`: list recorded real desktop client verification evidence. This is visible in read-only MCP mode.
- `record_agent_activity`: record a local desktop agent action, status, target chapter, changed files, context source, and context hash in `.novel-agent/agent_activity.jsonl`. This is a writer tool.
- `list_agent_activity`: list recorded local desktop agent activity and handoff events. This is visible in read-only MCP mode.
- `build_agent_activity_report`: check whether target-visible local desktop agent activity is specific enough for handoff, including a handoff quality score and deductions. This is visible in read-only MCP mode.
- `build_handoff_integrity_report`: check whether handoff text, target-chapter context fingerprint, activity context hashes, referenced files, pending proposals, and optional expected context hash are consistent before takeover. This is visible in read-only MCP mode.
- `build_handoff_range_report`: build a multi-chapter handoff report from chapter range readiness, handoff integrity, activity evidence, and range-scoped proposals. This is visible in read-only MCP mode.
- `build_chapter_range_delivery_packet`: build a read-only multi-chapter author delivery packet from range readiness, per-chapter delivery packets, handoff range status, delivery gates, and post-acceptance commands. This is visible in read-only MCP mode.
- `build_chapter_range_acceptance_plan`: build a read-only plan for durable writer operations after the author accepts several draft chapters. It checks author confirmation, returns `pre_write_review`, lists per-chapter write-session guards and state write scopes, blocks writer-step availability while author review is pending, and orders chapter records, proposal save/readiness/apply steps, activity logging, and the next session. This is visible in read-only MCP mode.
- `get_project_status`: return readiness, blockers, warnings, counts, latest desktop verification, latest agent activity, and recommended commands. This is visible in read-only MCP mode.
- `build_continuity_audit`: return a read-only continuity audit for chapter sequence, pending proposals, proposal validation, fact evidence, chapter visibility boundaries, source and research boundaries, handoff text, agent declarations, style samples, real desktop-client evidence, and prioritized `action_plan` groups. This is visible in read-only MCP mode.
- `verify_handoff_readiness`: verify whether an incoming desktop agent can safely take over a target chapter, including project status, proposals, handoff text, recent agent activity, outgoing agent activity when provided, agent capabilities, and real client evidence. This is visible in read-only MCP mode.
- `add_source_summary`: persist parsed source material with chapter visibility and writing constraints.
- `add_research_note`: persist web or external research with URL, visibility, and reliability.
- `declare_agent_capabilities`, `list_agent_capabilities`: record what the host agent supports, such as MCP, CLI, file read/write, web, PDF/OCR, or long context.
- `resolve_conflict`: record which version wins when sources or chapters disagree.
- `add_fact`, `update_fact`, `list_facts`, `supersede_fact`: maintain structured continuity facts.
- `build_chapter_context`: return chapter-safe context for a target chapter, including read-only `context_budget` section sizes, counts, warnings, recommendations, and read-first `context_brief` with chapter-range priority metadata.
- `build_chapter_context_brief`: return only the compact read-first `context_brief` plus a budget summary for small-context desktop agents. It is read-only and does not include the full chapter context tables.
- `prepare_chapter_session`: return a read-only chapter startup packet with health checks, handoff, recent agent activity, open threads, tool gates, workflow, chapter-safe context, context budget status, and read-first context brief.
- `record_chapter`: persist accepted chapter text metadata and tail.
- `record_style_sample`: store structured style metrics from accepted chapters.
- `check_chapter`: detect required phrase misses, forbidden phrase hits, future-setting leaks, character-state conflicts, fact conflicts, prop ownership conflicts, resolved-conflict violations, contract misses, shrinkage, and common model-like prose markers. Fact-backed issues include `fact_id` and `fact_context` so the host agent can see the recorded fact that was violated. The result also includes `revision_plan`, ordered from canon/fact repairs to chapter boundaries, required content, contract violations, draft integrity, and style polish.
- `diff_chapter_contract`: compare a draft with required beats, acceptance checks, forbidden moves, and future markers.
- `build_chapter_readiness`: return a read-only author-facing readiness report that combines `check_chapter` and `diff_chapter_contract` into must-fix, review, and improvement items. Compact fact-backed items preserve `fact_context`; `revision_plan` gives desktop agents the repair order and the relevant fact contexts.
- `build_chapter_revision_prompt`: return a read-only copyable revision prompt for a draft chapter, using `chapter-readiness`, `revision_plan`, and `fact_context` to tell a desktop agent what to repair and which facts to preserve. This is visible in read-only MCP mode.
- `build_chapter_revision_comparison`: return a read-only before/after revision report for a draft chapter, including resolved, new, and remaining continuity or contract issues plus the next revision prompt. This is visible in read-only MCP mode.
- `build_chapter_delivery_packet`: return a read-only author delivery packet for one draft chapter, including draft metadata, chapter readiness, copyable revision prompt, handoff report, chapter-scoped pending proposals, write boundaries, and post-acceptance commands. This is visible in read-only MCP mode.
- `build_chapter_acceptance_plan`: return a read-only post-acceptance write plan for one accepted draft chapter, including author-confirmation gates, required write order, skipped writes, and next-session command. This is visible in read-only MCP mode.
- `build_chapter_range_readiness`: return a read-only readiness report for a draft chapter range, including missing files, per-chapter status, scores, problem chapters, per-chapter follow-up commands, and compact per-chapter `revision_plan` summaries. This is visible in read-only MCP mode.
- `build_proposal_template`: return a blank post-write update template with chapter context and evidence requirements.
- `get_proposal_schema`: return the bundled JSON Schema for post-write proposals.
- `build_proposal_review`: return a read-only author review packet for a saved or inline proposal, including validation, diff, risk, next steps, and commands. This is visible in read-only MCP mode.
- `build_proposal_readiness`: return a read-only apply gate for a saved or inline proposal, including validation errors, author-review items, durable diff counts, risk, status, action plan, and commands. This is visible in read-only MCP mode.
- `propose_after_write_updates`, `validate_proposal`, `diff_proposal`, `apply_after_write_updates`, `reject_proposal`: confirm, preview, apply, or reject facts, character state, debts, and handoff after accepted writing.
- `create_snapshot`, `list_snapshots`, `restore_snapshot`: save or restore `.novel-agent/` state files. Restoring creates a backup snapshot first.
- `list_audit`: list audited write operations, touched files, result summaries, and migration notes.
- `update_after_write`: prepare archive update suggestions and the next handoff.
- `list_open_threads`: list unresolved plot debts, contracts, conflicts, and handoff notes.
- `build_handoff_report`: produce a next-agent report with target chapter, latest accepted chapter, handoff, recent agent activity, rules, open debts, contracts, pending proposals, required workflow, and `next_agent_brief`.
- `doctor`: check kit files, MCP tool table, project state files, write access, continuity coverage, optional local MCP config paths, and optional MCP start/tool-list behavior.

## Project State

Each novel project stores durable state in `.novel-agent/`:

- `manifest.json`: project title, stable `project_id`, target length, current chapter, and `state_schema_version`.
- `rules.json`: global required phrases, forbidden phrases, future markers, rename bans.
- `chapters.jsonl`: accepted chapter metadata, hashes, lengths, tails.
- `characters.json`: character state, aliases, relations, forbidden states.
- `debts.json`: open foreshadowing, plot debts, expected payoff windows.
- `contracts.jsonl`: chapter goals, required beats, checks, constraints.
- `facts.jsonl`: structured facts such as character state, prop ownership, event order, location, life state, rules, and relationships.
- `sources.jsonl`: summaries from files or old manuscripts parsed by the host agent.
- `research.jsonl`: external research summaries and URLs.
- `conflicts.jsonl`: resolved contradictions.
- `desktop_verifications.jsonl`: real desktop client verification records, required check results, evidence, and issues.
- `agent_activity.jsonl`: local desktop agent activity and handoff records, including action, chapter, status, changed files, context source, and context hash.
- `proposals.jsonl`: post-write update proposals waiting for confirmation or already applied.
- `audit.jsonl`: audited write operations, affected state files, result summaries, and state migrations.
- `agents.jsonl`: host agent capability declarations.
- `style_profile.json`: style continuity samples and aggregate metrics.
- `style.md`: style and de-AI preferences.
- `handoff.md`: latest next-chapter handoff.
- `snapshots/`: rollback copies of the state files.

Write commands through the CLI or MCP use `.novel-agent/.write.lock` to avoid concurrent writes from multiple agents. If a writer crashes, locks older than 1800 seconds are treated as stale and can be replaced by the next write.

This kit also ships `schemas/proposal.schema.json`, `schemas/desktop-pack-commands.schema.json`, `schemas/desktop-pack-commands-index.schema.json`, and the desktop-pack JSON schemas for pack index, first-three entry, local summary, project wizard, source intake, desktop next-step, desktop diagnostics, desktop pack readiness, starter brief, user steps, agent takeover, agent startup prompt, troubleshooting, author actions, acceptance review, desktop evidence session, desktop evidence runbook, desktop evidence review, writer mode approval, chapter session, handoff report, and desktop results. `proposal-template` and `build_proposal_template` reference the proposal schema; `proposal-schema` and `get_proposal_schema` return it. `desktop-pack` copies the command schema, the commands index schema, and the workflow schemas to `commands.schema.json`, `commands-index.schema.json`, `pack-index.schema.json`, `first-three.schema.json`, `local-summary.schema.json`, `project-wizard.schema.json`, `source-intake.schema.json`, `desktop-next-step.schema.json`, `desktop-diagnostics.schema.json`, `desktop-pack-readiness.schema.json`, `starter-brief.schema.json`, `user-steps.schema.json`, `agent-takeover.schema.json`, `agent-startup-prompt.schema.json`, `troubleshooting.schema.json`, `author-actions.schema.json`, `acceptance-review.schema.json`, `desktop-evidence-session.schema.json`, `desktop-evidence-runbook.schema.json`, `desktop-evidence-review.schema.json`, `writer-mode-approval.schema.json`, `chapter-session.schema.json`, `handoff-report.schema.json`, and `desktop-results.schema.json`; the generated `index.html` embeds twenty-three workflow schemas for local browser validation, and `pack-schema-check` verifies twenty-four target JSON files plus key generated-file references. Write commands automatically migrate older `.novel-agent/` state to the current `state_schema_version` before durable changes, then append an audit row.

Use `assets/review-panel.html` when an author wants a local static page for reading proposal, diff, audit, or snapshot JSON before confirming updates.

## Chapter Safety

- Do not expose notes with `reveal_after_chapter >= target_chapter`.
- Do not expose notes whose chapter range starts after the target chapter.
- Treat future titles, aliases, and configured future markers as forbidden in early chapters.
- If a source is useful but not yet visible, refer to it only as unavailable future material.

## Writing Rules

- Start each chapter task from `build_chapter_context` or `prepare_chapter_session`, or from `build_chapter_context_brief` when the host context window is small. Read `context_brief`, and inspect `context_budget` before drafting in long projects.
- Preserve accepted facts, names, sequence, promises, and constraints.
- Treat `.novel-agent/facts.jsonl` as the highest precision continuity layer after direct user instructions and accepted chapter text.
- Expect `check-chapter` to flag structured fact conflicts for timeline order, prop ownership, location state, relationship reversals, and life state when the relevant facts and chapter wording are explicit. Use `chapter-revision-prompt`, or the returned `fact_context` and `revision_plan`, to revise against the recorded fact before polishing prose. Use `chapter-revision-compare` when a previous draft file is available to verify that the revision reduced or cleared the issues.
- Keep character voice and state consistent with `.novel-agent/characters.json`.
- Advance or protect open debts from `.novel-agent/debts.json`; do not silently resolve them.
- When a chapter changes canon, put the change in a post-write proposal; do not silently edit durable state.
- A proposal must conform to `schemas/proposal.schema.json` and include evidence for each fact, character update, and debt update before `apply-after-write` can apply it. When a chapter file is available, each evidence quote must appear in that chapter text.
- After one chapter is accepted, call `chapter-acceptance-plan` / `build_chapter_acceptance_plan` before durable writer commands. Read its `pre_write_review`, run `prepare_session_for_write_guard`, then require `write-session-check` / `check_write_session` to return `ready` before `record-chapter`, `propose-after-write`, `apply-after-write`, or reviewed `record-agent-activity`. For several accepted chapters, call `chapter-range-acceptance-plan` / `build_chapter_range_acceptance_plan`, read its `pre_write_review`, and run the per-chapter guard commands before each chapter's durable writer commands.
- Run `proposal-readiness` before `apply-after-write`; `needs_revision`, `rejected`, and `already_applied` must not be applied, and `needs_author_review` needs explicit author approval before applying.
- Treat `validation.risk.level=high` as requiring explicit author review. Common high-risk changes include death, betrayal, identity reveal, major relationship reversal, prop transfer, and plot debt payoff.
- If a durable update looks wrong after applying, inspect `list-snapshots` and use `restore-snapshot` instead of editing state files by hand.
- Prefer action, dialogue, sensory details, object changes, and consequences over explanation.
- In de-AI revision, preserve plot facts and chapter length; reduce template transitions, meaning-summary sentences, abstract emotion labels, repeated dialogue tags, and slogan-like endings.

## Output Discipline

For writing tasks, return:

- Basis used: accepted chapters, visible sources, rules, debts, contracts.
- Draft or revision.
- Continuity check result.
- Archive updates needed: characters, debts, contracts, sources, handoff.

If evidence is missing, say exactly what is missing. Do not turn inference into canon.
