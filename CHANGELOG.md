# Changelog

## 2026-07-07

### Windows Runtime Release Preparation

- Added a Windows runtime release guide covering manual Windows builds, executable checks, zip packaging, GitHub release upload, and no-Python Windows handoff bundle creation.
- Added a GitHub Actions template for building and verifying `long-novel-agent.exe` and `long-novel-agent-mcp.exe` on `windows-latest`.
- Linked the Windows release guide from the English README, Chinese README, installation guide, and no-Python handoff guide.

Validation:

- `python3 cli.py standalone-build --target-os windows --output-dir release/runtime-windows --format json`
- Windows `.exe` build was not produced on this macOS arm64 machine; PyInstaller Windows runtime must be built on Windows.

## 2026-07-06

### Architecture And Capability Documentation

- Reworked the root README and Chinese README to explain the full local architecture, component responsibilities, state model, capability map, writing flow, read-only/writer boundaries, desktop packs, and no-Python handoff bundles.
- Expanded `docs/architecture.md` with system layers, state ownership, read/check/write paths, MCP/CLI surfaces, desktop-pack architecture, no-Python handoff architecture, and boundaries.
- Added `docs/system-overview.zh-CN.md` as a Chinese end-to-end architecture and function guide for authors, developers, and desktop agents.
- Expanded `docs/desktop-agent-workflow.md` with role split, required sequence, writer path, and unsafe actions.

Validation:

- `git diff --check`
- local path leakage scan over repository docs and sources
- `python3 -m py_compile cli.py server.py install.py scripts/verify_agent_kit.py scripts/adversarial_release_check.py`
- `python3 scripts/adversarial_release_check.py`

### Initial Open Source Release

- Published Long Novel Agent Kit as a standalone local desktop-agent continuity toolkit.
- Included CLI, stdio MCP server, skill instructions, installer, schemas, review panel, examples, and regression tests.
- Added no-Python handoff bundle support through `standalone-build` and `desktop-handoff-bundle`.
- Added release-grade adversarial verification through `scripts/adversarial_release_check.py`.

Validation:

- `python -m py_compile cli.py server.py install.py scripts/verify_agent_kit.py scripts/adversarial_release_check.py`
- `python scripts/verify_agent_kit.py`
- `python scripts/adversarial_release_check.py`
